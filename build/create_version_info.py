# build/create_version_info.py
import sys
import os

def create_version_file(version_str):
    # Ensure version has 4 parts (e.g., 1.1.8 -> 1.1.8.0)
    parts = version_str.split('.')
    while len(parts) < 4:
        parts.append('0')
    
    # Create the tuple format: (1, 1, 8, 0)
    version_tuple = f"({', '.join(parts)})"
    # Create the string format: "1.1.8.0"
    version_full_str = '.'.join(parts)

    content = f"""
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={version_tuple},
    prodvers={version_tuple},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringStruct('CompanyName', 'Erez C137'),
        StringStruct('FileDescription', 'NetSpeedTray'),
        StringStruct('FileVersion', '{version_full_str}'),
        StringStruct('InternalName', 'NetSpeedTray'),
        StringStruct('LegalCopyright', 'Copyright (c) Erez C137'),
        StringStruct('OriginalFilename', 'NetSpeedTray.exe'),
        StringStruct('ProductName', 'NetSpeedTray'),
        StringStruct('ProductVersion', '{version_str}'),
      ]
    ),
    VarFileInfo([VarStruct('Translation', [0x0409, 1252])])
  ]
)
"""
    
    output_path = os.path.join(os.path.dirname(__file__), 'version_info.txt')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(content.strip())
    
    print(f"Generated version_info.txt for version {version_str}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Error: Version argument missing")
        sys.exit(1)
    
    create_version_file(sys.argv[1])