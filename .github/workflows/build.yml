name: Build Windows Executable

on:
  push:
    branches: [ main ]

jobs:
  build:
    runs-on: windows-latest

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.10'

    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install pdfplumber openpyxl Pillow pytesseract pyinstaller

    - name: Build with PyInstaller
      run: |
        pyinstaller --noconfirm --onedir --windowed --name "MotoreESG" app.py

    - name: Upload artifact
      uses: actions/upload-artifact@v4
      with:
        name: MotoreESG-Windows
        path: dist/
