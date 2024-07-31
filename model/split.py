import json
import random
import sys

def split_jsonl_file(input_file, output_prefix, percentages):
    with open(input_file, 'r') as infile:
        lines = infile.readlines()
    
    total_lines = len(lines)
    indices = list(range(total_lines))
    
    random.shuffle(indices)
    
    for percentage in percentages:
        num_samples = int(total_lines * (percentage / 100))
        selected_indices = set(indices[:num_samples])
        
        output_file = f'{output_prefix}_{percentage}.jsonl'
        with open(output_file, 'w') as outfile:
            for i, line in enumerate(lines):
                if i in selected_indices:
                    outfile.write(line)
                    
        print(f"Saved {percentage}% of data to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python split_jsonl.py <input_file> <output_prefix> <percentages>")
        print("Example: python split_jsonl.py data.jsonl output 20,40,60,80,100")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_prefix = sys.argv[2]
    percentages = list(map(int, sys.argv[3].split(',')))
    
    split_jsonl_file(input_file, output_prefix, percentages)
