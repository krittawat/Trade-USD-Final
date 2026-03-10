import os
import re

def fix_symbols_in_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        try:
            with open(filepath, 'r', encoding='latin-1') as f:
                content = f.read()
        except Exception as e:
            print(f"Skipping {filepath}: {e}")
            return

    # Replace [A-Z]{6}m with [A-Z]{6}c
    # e.g. XAUUSDc -> XAUUSDc
    new_content = re.sub(r'([A-Z]{6})m\b', r'\1c', content)
    
    if content != new_content:
        print(f"Fixing {filepath}...")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(new_content)

def main():
    backend_dir = r'd:\VibeCode\Trade\backend'
    print(f"Scanning {backend_dir} for *m symbols...")
    
    count = 0
    for root, dirs, files in os.walk(backend_dir):
        for file in files:
            if file.endswith('.py'):
                fix_symbols_in_file(os.path.join(root, file))
                count += 1
                
    print(f"Scanned {count} files. Done.")

if __name__ == '__main__':
    main()
