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

    valid_extensions = ('.pdf', '.jpg', '.jpeg', '.png')
    
    file_list = []
    for root_dir, _, files in os.walk(cartella):
        for filename in files:
            if filename.lower().endswith(valid_extensions):
                file_list.append((root_dir, filename))
    
    if not file_list:
        messagebox.showwarning("Attenzione", "Nessun file valido (.pdf, .jpg, .jpeg, .png) trovato nella cartella o nelle sottocartelle.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Consumi e Forniture"
    
    current_time_str = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    ws.append([f"Estrazione dati dalle bollette in data {current_time_str}"])
    ws.cell(row=1, column=1).font = Font(size=12, bold=True, color="1F497D")
    
    ws.append([])
    
    headers = ["Azienda", "Nome File", "Periodo", "Anno", "Quantità", "Unità di misura"]
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
        
        quantita = "Non rilevato"
        unita_misura = "Non rilevata"
        periodo = "Non rilevato"
        anno = "Non rilevato"
        
        text = ""
        try:
            if ext.endswith('.pdf'):
                with pdfplumber.open(file_path) as pdf:
                    if len(pdf.pages) > 0:
                        page = pdf.pages[0]
                        text = page.extract_text()
                        if text:
                            text_lower = text.lower()
                            if any(term in text_lower for term in ["kwh", "smc", "litri", "kg", "mc", "energia", "gas", "quantità"]):
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

                                # Estrazione Quantità e Unità di Misura dinamica
                                try:
                                    words = page.extract_words(extra_attrs=["size"])
                                    candidates = []
                                    for i, word in enumerate(words):
                                        w_text = word['text']
                                        unit_match = re.match(r'^(?:kWh|KWh|KWH|Smc|SMC|smc|mc|MC|Litri|litri|L|kg|KG|Kg)$', w_text)
                                        if unit_match:
                                            detected_unit = w_text
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
                                                            'unita': detected_unit,
                                                            'size': words[j].get('size', 0)
                                                        })
                                    if candidates:
                                        candidates.sort(key=lambda x: x['size'], reverse=True)
                                        quantita = candidates[0]['valore']
                                        unita_misura = candidates[0]['unita']
                                    else:
                                        match_qty = re.search(r'(\d+[\.,]?\d*)\s*(kWh|KWh|KWH|Smc|SMC|smc|mc|MC|Litri|litri|L|kg|KG|Kg)', text)
                                        if match_qty:
                                            quantita = match_qty.group(1)
                                            unita_misura = match_qty.group(2)
                                except Exception:
                                    match_qty = re.search(r'(\d+[\.,]?\d*)\s*(kWh|KWh|KWH|Smc|SMC|smc|mc|MC|Litri|litri|L|kg|KG|Kg)', text)
                                    if match_qty:
                                        quantita = match_qty.group(1)
                                        unita_misura = match_qty.group(2)
            elif ext.endswith(('.jpg', '.jpeg', '.png')):
                try:
                    img_text = pytesseract.image_to_string(Image.open(file_path))
                    if img_text:
                        text = img_text
                        text_lower = text.lower()
                        if any(term in text_lower for term in ["kwh", "smc", "litri", "kg", "mc", "energia", "gas", "quantità"]):
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
                            
                            match_qty = re.search(r'(\d+[\.,]?\d*)\s*(kWh|KWh|KWH|Smc|SMC|smc|mc|MC|Litri|litri|L|kg|KG|Kg)', text)
                            if match_qty:
                                quantita = match_qty.group(1)
                                unita_misura = match_qty.group(2)
                except Exception:
                    pass

            if quantita != "Non rilevato":
                row_idx = ws.max_row + 1
                ws.append([azienda, filename, periodo, anno, quantita, unita_misura])
                
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
        messagebox.showwarning("Attenzione", "Nessuna bolletta valida è stata trovata nei file analizzati.")
        return

    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_filename = f"Consumi_Forniture_{azienda.replace(' ', '_')}.xlsx"
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

# Configurazione Interfaccia Grafica Moderna
root = tk.Tk()
root.title("Estrai Quantità Bollette")
root.geometry("500x380")
root.resizable(False, False)
root.configure(bg="#f8fafc")

FONT_FAMILY = "Segoe UI"

# Intestazione grafica
lbl_title = tk.Label(root, text="Analizzatore Bollette", font=(FONT_FAMILY, 15, "bold"), bg="#f8fafc", fg="#1e293b")
lbl_title.pack(anchor="w", padx=28, pady=(24, 2))

lbl_subtitle = tk.Label(root, text="Estrai quantità e unità di misura in Excel", font=(FONT_FAMILY, 9), bg="#f8fafc", fg="#64748b")
lbl_subtitle.pack(anchor="w", padx=28, pady=(0, 18))

# Sezione Azienda
tk.Label(root, text="Nome Azienda:", font=(FONT_FAMILY, 10, "bold"), bg="#f8fafc", fg="#334155").pack(anchor="w", padx=28, pady=(4, 4))
entry_azienda = tk.Entry(root, font=(FONT_FAMILY, 10), relief="solid", bd=1, highlightthickness=0)
entry_azienda.pack(padx=28, pady=2, fill="x", ipady=5)

# Sezione Percorso Cartella
tk.Label(root, text="Cartella Bollette (PDF / Immagini):", font=(FONT_FAMILY, 10, "bold"), bg="#f8fafc", fg="#334155").pack(anchor="w", padx=28, pady=(12, 4))

frame_path = tk.Frame(root, bg="#f8fafc")
frame_path.pack(padx=28, pady=2, fill="x")

entry_path = tk.Entry(frame_path, font=(FONT_FAMILY, 10), relief="solid", bd=1)
entry_path.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))

btn_browse = tk.Button(frame_path, text="Sfoglia...", command=seleziona_cartella, font=(FONT_FAMILY, 9, "bold"), bg="#e2e8f0", fg="#334155", relief="flat", cursor="hand2", padx=14, pady=5)
btn_browse.pack(side="right")

# Bottone Principale d'Azione
btn_run = tk.Button(root, text="Analizza ed Esporta in Excel", command=avvia_estrazione, bg="#0284c7", fg="white", font=(FONT_FAMILY, 10, "bold"), relief="flat", cursor="hand2", pady=10)
btn_run.pack(padx=28, pady=(24, 10), fill="x")

lbl_status = tk.Label(root, text="", font=(FONT_FAMILY, 9, "italic"), bg="#f8fafc", fg="#64748b")
lbl_status.pack(padx=28, pady=(0, 10))

root.mainloop()
