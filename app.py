import os
import re
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox
import pdfplumber
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from PIL import Image
import pytesseract

def analizza_bollette(azienda, cartella, status_label):
    if not azienda or not cartella:
        messagebox.showerror("Errore", "Inserisci il nome dell'azienda e seleziona una cartella valida.")
        return

    if not os.path.exists(cartella):
        messagebox.showerror("Errore", "La cartella specificata non esiste.")
        return

    # Estensioni ammesse con priorità e scarto a priori di tutto il resto
    valid_extensions = ('.pdf', '.jpg', '.jpeg', '.png')
    
    file_list = []
    for root_dir, _, files in os.walk(cartella):
        for filename in files:
            # Scarta a priori i file con estensioni non supportate
            if filename.lower().endswith(valid_extensions):
                file_list.append((root_dir, filename))
    
    if not file_list:
        messagebox.showwarning("Attenzione", "Nessun file valido (.pdf, .jpg, .jpeg, .png) trovato nella cartella o nelle sottocartelle.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Consumi Elettrici"
    
    # Intestazione del report in alto con data e ora correnti del PC
    current_time_str = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    ws.append([f"Estrazione dati dalle bollette in data {current_time_str}"])
    ws.cell(row=1, column=1).font = Font(size=12, bold=True, color="1F497D")
    
    # Riga vuota di separazione
    ws.append([])
    
    # Intestazioni tabella aggiornate con "Unità di misura"
    headers = ["Azienda", "Nome File", "Periodo", "Anno", "Consumo (kWh)", "Unità di misura"]
    ws.append(headers)
    
    header_row_idx = 3
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=header_row_idx, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    mesi_mappa = {
        'gennaio': 'Gennaio', 'febbraio': 'Febbraio', 'marzo': 'Marzo', 'aprile': 'Aprile',
        'maggio': 'Maggio', 'giugno': 'Giugno', 'luglio': 'Luglio', 'agosto': 'Agosto',
        'settembre': 'Settembre', 'ottobre': 'Ottobre', 'novembre': 'Novembre', 'dicembre': 'Dicembre',
        'gen': 'Gen', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr',
        'mag': 'Mag', 'giu': 'Giu', 'lug': 'Lug', 'ago': 'Ago',
        'set': 'Set', 'ott': 'Ott', 'nov': 'Nov', 'dic': 'Dic'
    }

    success_count = 0
    total_files = len(file_list)
    valid_count = 0
    
    for root_dir, filename in file_list:
        file_path = os.path.join(root_dir, filename)
        abs_path = os.path.abspath(file_path)
        ext = filename.lower()
        
        consumo = "Non rilevato"
        periodo = "Non rilevato"
        anno = "Non rilevato"
        unita_misura = "kWh"
        
        text = ""
        try:
            if ext.endswith('.pdf'):
                with pdfplumber.open(file_path) as pdf:
                    if len(pdf.pages) > 0:
                        page = pdf.pages[0]
                        text = page.extract_text()
                        if text:
                            text_lower = text.lower()
                            if "energia elettrica" in text_lower or "kwh" in text_lower:
                                # Estrazione Anno
                                years = re.findall(r'\b(20\d{2})\b', text)
                                if years:
                                    anno = years[0]
                                    
                                # Estrazione Periodo
                                found_mesi = []
                                for m_key, m_val in mesi_mappa.items():
                                    if re.search(r'\b' + m_key + r'\b', text_lower):
                                        if m_val not in found_mesi:
                                            found_mesi.append(m_val)
                                if found_mesi:
                                    periodo = found_mesi[0] if len(found_mesi) == 1 else f"{found_mesi[0]} - {found_mesi[-1]}"

                                # Estrazione Consumo (kWh) con logica dimensione carattere e filtro F1/F2/F3
                                try:
                                    words = page.extract_words(extra_attrs=["size"])
                                    candidates = []
                                    for i, word in enumerate(words):
                                        w_text = word['text']
                                        if re.match(r'^(?:kWh|KWh|KWH)$', w_text):
                                            for j in range(max(0, i-3), i):
                                                prev_word = words[j]['text']
                                                clean_prev = prev_word.replace('.', '').replace(',', '.')
                                                if re.match(r'^\d+[\.,]?\d*$', clean_prev):
                                                    is_band = False
                                                    for k in range(max(0, j-2), min(len(words), j+3)):
                                                        if re.match(r'^F[1-3]$', words[k]['text'], re.IGNORECASE):
                                                            is_band = True
                                                            break
                                                    if not is_band:
                                                        candidates.append({
                                                            'valore': prev_word,
                                                            'size': words[j].get('size', 0)
                                                        })
                                    if candidates:
                                        candidates.sort(key=lambda x: x['size'], reverse=True)
                                        consumo = candidates[0]['valore']
                                    else:
                                        match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                                        if match_kwh:
                                            consumo = match_kwh.group(1)
                                except Exception:
                                    match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                                    if match_kwh:
                                        consumo = match_kwh.group(1)
            elif ext.endswith(('.jpg', '.jpeg', '.png')):
                try:
                    img_text = pytesseract.image_to_string(Image.open(file_path))
                    if img_text:
                        text = img_text
                        text_lower = text.lower()
                        if "energia elettrica" in text_lower or "kwh" in text_lower:
                            years = re.findall(r'\b(20\d{2})\b', text)
                            if years:
                                anno = years[0]
                            found_mesi = []
                            for m_key, m_val in mesi_mappa.items():
                                if re.search(r'\b' + m_key + r'\b', text_lower):
                                    if m_val not in found_mesi:
                                        found_mesi.append(m_val)
                            if found_mesi:
                                periodo = found_mesi[0] if len(found_mesi) == 1 else f"{found_mesi[0]} - {found_mesi[-1]}"
                            
                            match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                            if match_kwh:
                                consumo = match_kwh.group(1)
                except Exception:
                    pass

            if consumo != "Non rilevato":
                row_idx = ws.max_row + 1
                ws.append([azienda, filename, periodo, anno, consumo, unita_misura])
                
                # Hyperlink sul nome file (Colonna 2)
                cell_file = ws.cell(row=row_idx, column=2)
                cell_file.hyperlink = abs_path
                cell_file.font = Font(color="0563C1", underline="single")
                
                valid_count += 1
        except Exception:
            pass
            
        success_count += 1
        status_label.config(text=f"Scansionati ({success_count}/{total_files}) - Validi trovati: {valid_count}")
        status_label.update()

    if valid_count == 0:
        messagebox.showwarning("Attenzione", "Nessuna bolletta dell'energia elettrica valida è stata trovata nei file analizzati.")
        return

    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_filename = f"Consumi_Elettrici_{azienda.replace(' ', '_')}.xlsx"
    output_path = os.path.join(desktop_path, output_filename)
    
    wb.save(output_path)
    status_label.config(text="Completato!")
    messagebox.showinfo("Successo", f"File Excel salvato con successo sul Desktop:\n{output_filename}")

def seleziona_cartella():
    path = filedialog.askdirectory()
    if path:
        entry_path.delete(0, tk.END)
        entry_path.insert(0, path)

def avvia_estrazione():
    azienda = entry_azienda.get()
    cartella = entry_path.get()
    analizza_bollette(azienda, cartella, lbl_status)

root = tk.Tk()
root.title("Estrai Consumi Bollette")
root.geometry("450x300")
root.resizable(False, False)

tk.Label(root, text="Nome Azienda:", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(20, 5))
entry_azienda = tk.Entry(root, width=50, font=("Arial", 10))
entry_azienda.pack(padx=20, pady=5)

tk.Label(root, text="Cartella Bollette (PDF/Img):", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(10, 5))

frame_path = tk.Form = tk.Frame(root) if hasattr(tk, 'Form') else tk.Frame(root) # standard frame
frame_path.pack(padx=20, pady=5, fill="x")

entry_path = tk.Entry(frame_path, width=38, font=("Arial", 10))
entry_path.pack(side="left", padx=(0, 5))

btn_browse = tk.Button(frame_path, text="Sfoglia", command=seleziona_cartella, font=("Arial", 9))
btn_browse.pack(side="left")

btn_run = tk.Button(root, text="Analizza ed Esporta in Excel", command=avvia_estrazione, bg="#4CAF50", fg="white", font=("Arial", 10, "bold"))
btn_run.pack(padx=20, pady=25, fill="x")

lbl_status = tk.Label(root, text="", font=("Arial", 9, "italic"), fg="gray")
lbl_status.pack(padx=20, pady=(0, 10))

root.mainloop()
