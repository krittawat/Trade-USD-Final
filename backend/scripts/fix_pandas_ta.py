"""
Batch fix pandas_ta for Python 3.14 / newer pandas compatibility.

Fixes:
1. .category = "xxx" on Series -> wrapped in try/except
2. all(isnan(xxx)) -> xxx.isna().all()
"""
import os
import re

BASE = r"C:\Users\Javis\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\pandas_ta"
fixed_files = []

for root, dirs, files in os.walk(BASE):
    for fname in files:
        if not fname.endswith(".py"):
            continue
        path = os.path.join(root, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception:
            continue

        original = content

        # Fix 1: .category = "xxx" -> try/except
        # Match: <indent><var>.category = "<category>"
        def fix_category(m):
            indent = m.group(1)
            var = m.group(2)
            cat = m.group(3)
            return f'{indent}try:\n{indent}    {var}.category = "{cat}"\n{indent}except (AttributeError, ValueError):\n{indent}    pass'

        content = re.sub(
            r'^(\s+)(\w+)\.category\s*=\s*"([^"]+)"\s*$',
            fix_category,
            content,
            flags=re.MULTILINE,
        )

        # Fix 2: all(isnan(xxx)) -> xxx.isna().all()
        content = re.sub(
            r"all\(isnan\((\w+)\)\)",
            r"\1.isna().all()",
            content,
        )

        # Fix 3: Clean up unused isnan imports
        if "isnan" in original and "isnan" not in content.split("\n", 50)[-1]:
            # Check if isnan is still used anywhere in the content (excluding imports)
            lines = content.split("\n")
            import_lines = []
            code_lines = []
            for line in lines:
                if "import" in line and "isnan" in line:
                    import_lines.append(line)
                else:
                    code_lines.append(line)
            
            code_text = "\n".join(code_lines)
            if "isnan" not in code_text:
                # Remove isnan from imports
                content = content.replace("from numpy import isnan, nan", "from numpy import nan")
                content = content.replace("from numpy import isnan\n", "")

        if content != original:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            rel = os.path.relpath(path, BASE)
            fixed_files.append(rel)
            print(f"  Fixed: {rel}")

print(f"\nTotal files fixed: {len(fixed_files)}")
