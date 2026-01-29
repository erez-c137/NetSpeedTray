
import json
import os
import sys

def check_translations():
    # Find locales directory relative to the script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, "../../..")) # src/netspeedtray/tests -> project root
    
    # Try a few candidate paths for locales
    candidates = [
        os.path.join(script_dir, "../constants/locales"),
        os.path.join(project_root, "src/netspeedtray/constants/locales"),
        "src/netspeedtray/constants/locales"
    ]
    
    locales_dir = None
    for cand in candidates:
        if os.path.isdir(cand):
            locales_dir = cand
            break
            
    if not locales_dir:
        print("Error: Could not find locales directory.")
        sys.exit(1)
        
    en_us_path = os.path.join(locales_dir, 'en_US.json')
    if not os.path.exists(en_us_path):
        print(f"Error: Could not find base translation file at {en_us_path}")
        sys.exit(1)
        
    with open(en_us_path, 'r', encoding='utf-8') as f:
        en_us = json.load(f)

    untranslated_report = {}

    for filename in os.listdir(locales_dir):
        if filename == 'en_US.json' or not filename.endswith('.json'):
            continue
        
        file_path = os.path.join(locales_dir, filename)
        with open(file_path, 'r', encoding='utf-8') as f:
            lang = json.load(f)
        
        missing = []
        identical = []
        for key, value in en_us.items():
            if key == "//": continue
            if key not in lang:
                missing.append(key)
            elif lang[key] == value:
                # Some things *should* be identical (arrows, units, short labels)
                # We can whitelist those if we want to avoid noise, but for now we list them.
                identical.append(key)
        
        if missing or identical:
            untranslated_report[filename] = {
                "missing": missing,
                "identical": identical
            }

    if untranslated_report:
        print(json.dumps(untranslated_report, indent=2, ensure_ascii=False))
    else:
        print("All translations are up to date and fully unique!")

if __name__ == "__main__":
    check_translations()
