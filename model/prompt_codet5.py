import os
import torch
import datasets
import transformers
import numpy as np
import pandas as pd
from datasets import load_dataset, Dataset
from sklearn.metrics import accuracy_score, precision_recall_fscore_support,precision_score,recall_score,f1_score
import sys

os.environ['CUDA_VISIBLE_DEVICES'] = '0'
seed = 42
batch_size = 16
num_class = 2
max_seq_l = 256
lr = 1e-5
num_epochs = 20
use_cuda = True
model_name = "codet5"
pretrainedmodel_path = "models/codet5-base"
dataset_path = "bigvulfunc.json"

# load the dataset and make statistics
bigvulfunc = pd.read_json(dataset_path)
target_distr=pd.value_counts(bigvulfunc.target)
proportion = pd.value_counts(bigvulfunc.target, normalize=True)
print(len(bigvulfunc))
print(target_distr)
print(proportion)





# processing and splitting the dataset
dataset = Dataset.from_dict(bigvulfunc)
dataset = dataset.remove_columns(['project', 'commit_id', 'idx'])
traintest = dataset.train_test_split(test_size=0.2, seed=seed)
validationtest = traintest['test'].train_test_split(test_size=0.5, seed=seed)
train_val_test = {}
train_val_test['train'] = traintest['train']
train_val_test['validation'] = validationtest['train']
train_val_test['test'] = validationtest['test']
train_val_test


from openprompt.data_utils import InputExample
dataset = {}
for split in ['train', 'validation', 'test']:
    dataset[split] = []
    for data in train_val_test[split]:
        input_example = InputExample(text_a = data['func'], label=int(data['target']))
        dataset[split].append(input_example)


# load plm
from openprompt.plms import load_plm
plm, tokenizer, model_config, WrapperClass = load_plm(model_name, pretrainedmodel_path)

# construct hard template
from openprompt.prompts import ManualTemplate
template_text = ' the code {"placeholder":"text_a"} is {"mask"}'

mytemplate = ManualTemplate(tokenizer=tokenizer, text=template_text)

# DataLoader
from openprompt import PromptDataLoader
train_dataloader = PromptDataLoader(dataset=dataset["train"], template=mytemplate, tokenizer=tokenizer,
    tokenizer_wrapper_class=WrapperClass, max_seq_length=max_seq_l, batch_size=batch_size,shuffle=True,
    teacher_forcing=False, predict_eos_token=False, truncate_method="head",decoder_max_length=3)
validation_dataloader = PromptDataLoader(dataset=dataset["validation"], template=mytemplate, tokenizer=tokenizer,
    tokenizer_wrapper_class=WrapperClass, max_seq_length=max_seq_l, batch_size=batch_size,shuffle=True,
    teacher_forcing=False, predict_eos_token=False, truncate_method="head",decoder_max_length=3)
test_dataloader = PromptDataLoader(dataset=dataset["test"], template=mytemplate, tokenizer=tokenizer,
    tokenizer_wrapper_class=WrapperClass, max_seq_length=max_seq_l, batch_size=batch_size,shuffle=True,
    teacher_forcing=False, predict_eos_token=False, truncate_method="head",decoder_max_length=3)

# define the verbalizer
from openprompt.prompts import ManualVerbalizer
myverbalizer = ManualVerbalizer(tokenizer, num_classes=num_class, label_words=[["vulnerable","bad"], ["nonvulnerable","good"]])

# define prompt model for classification
from openprompt import PromptForClassification
prompt_model = PromptForClassification(plm=plm,template=mytemplate, verbalizer=myverbalizer, freeze_plm=False)
if use_cuda:
    prompt_model=  prompt_model.cuda()

from transformers import  AdamW, get_linear_schedule_with_warmup

loss_func = torch.nn.CrossEntropyLoss()
no_decay = ['bias', 'LayerNorm.weight']

# it's always good practice to set no decay to biase and LayerNorm parameters
optimizer_grouped_parameters1 = [
    {'params': [p for n, p in prompt_model.named_parameters() if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
    {'params': [p for n, p in prompt_model.named_parameters() if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
]
# Using different optimizer for prompt parameters and model parameters
optimizer_grouped_parameters2 = [
    {'params': [p for n,p in prompt_model.template.named_parameters() if "raw_embedding" not in n]}
]
optimizer1 = AdamW(optimizer_grouped_parameters1, lr=lr)    # learning rate for model parameters
optimizer2 = AdamW(optimizer_grouped_parameters2, lr=5e-4)  # learning rate for prompt parameters

num_training_steps = num_epochs * len(train_dataloader)
scheduler1 = get_linear_schedule_with_warmup(optimizer1, num_warmup_steps=0, num_training_steps=num_training_steps)  #set warmup steps
scheduler2 = get_linear_schedule_with_warmup(optimizer2, num_warmup_steps=0, num_training_steps=num_training_steps)  #set warmup steps


from tqdm.auto import tqdm
def test(prompt_model, test_dataloader):
    num_test_steps = len(test_dataloader)
    progress_bar = tqdm(range(num_test_steps))
    allpreds = []
    alllabels = []
    with torch.no_grad():
        for step, inputs in enumerate(test_dataloader):
            if use_cuda:
                inputs = inputs.cuda()
            logits = prompt_model(inputs)
            labels = inputs['label']
            progress_bar.update(1)
            alllabels.extend(labels.cpu().tolist())
            allpreds.extend(torch.argmax(logits, dim=-1).cpu().tolist())
        acc = accuracy_score(alllabels, allpreds)
        precision = precision_score(alllabels, allpreds)
        recall = recall_score(alllabels, allpreds)

        precisionwei, recallwei, f1wei, _ = precision_recall_fscore_support(alllabels, allpreds, average='weighted')
        precisionmi, recallmi, f1mi, _ = precision_recall_fscore_support(alllabels, allpreds, average='binary')
        # precision, recall, f1, _ = precision_recall_fscore_support(alllabels, allpreds, average=None)


        print("acc: {}   precision :{}  recall:{} weighted-f1: {}  binary-f1: {}".format(acc, precision,recall,f1wei, f1mi))
    return acc, precision,recall,f1wei, f1mi


output_dir = "soft_prompt log"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

progress_bar = tqdm(range(num_training_steps))
bestmetric = 0
bestepoch = 0
for epoch in range(num_epochs):
    # train
    tot_loss = 0
    for step, inputs in enumerate(train_dataloader):
        if use_cuda:
            inputs = inputs.cuda()
        logits = prompt_model(inputs)
        labels = inputs['label']
        loss = loss_func(logits, labels)
        loss.backward()
        tot_loss += loss.item()
        optimizer1.step()
        optimizer1.zero_grad()
        scheduler1.step()
        optimizer2.step()
        optimizer2.zero_grad()
        scheduler2.step()
        progress_bar.update(1)
    print("\nEpoch {}, average loss: {}".format(epoch, tot_loss / (step + 1)), flush=True)

    # validate
    print('\n\nepoch{}------------validate------------'.format(epoch))
    acc, precision,recall,f1wei, f1mi = test(prompt_model, validation_dataloader)
    if f1wei > bestmetric:
        bestmetric = f1mi
        bestepoch = epoch
        torch.save(prompt_model.state_dict(), f"{output_dir}/best.ckpt")

    # test
    print('\n\nepoch{}------------test------------'.format(epoch))
    acc, precision, recall, f1wei, f1mi = test(prompt_model, test_dataloader)


