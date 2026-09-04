import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox
import pdfplumber
import openpyxl

def analizza_bollette(azienda, cartella, status_label):
    if not azienda or not cartella:
        messagebox.showerror("Errore", "Inserisci il nome dell'azienda e seleziona una cartella valida.")
        return

    if not os.path.exists(cartella):
        messagebox.showerror("Errore", "La cartella specificata non esiste.")
        return

    pdf_files = []
    for root_dir, _, files in os.walk(cartella):
        for filename in files:
            if filename.lower().endswith('.pdf'):
                pdf_files.append((root_dir, filename))
    
    if not pdf_files:
        messagebox.showwarning("Attenzione", "Nessun file PDF trovato nella cartella o nelle sottocartelle.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Consumi Elettrici"
    
    ws.append(["Azienda", "Nome File", "Percorso", "Consumo (kWh)"])
    
    for col in range(1, 5):
        ws.cell(row=1, column=col).font = openpyxl.styles.Font(bold=True)

    success_count = 0
    total_files = len(pdf_files)
    valid_count = 0
    
    for root_dir, filename in pdf_files:
        file_path = os.path.join(root_dir, filename)
        abs_path = os.path.abspath(file_path)
        rel_path = os.path.relpath(file_path, cartella)
        
        consumo = "Non rilevato"
        try:
            with pdfplumber.open(file_path) as pdf:
                if len(pdf.pages) > 0:
                    page = pdf.pages[0]
                    text = page.extract_text()
                    if text:
                        text_lower = text.lower()
                        if "energia elettrica" in text_lower or "kwh" in text_lower:
                            # Estrazione avanzata basata su parole, dimensione carattere e filtro fasce F1/F2/F3
                            try:
                                words = page.extract_words(extra_attrs=["size"])
                                candidates = []
                                
                                for i, word in enumerate(words):
                                    w_text = word['text']
                                    # Cerca l'unità di misura kWh
                                    if re.match(r'^(?:kWh|KWh|KWH)$', w_text):
                                        # Cerca il numero nelle parole immediatamente precedenti
                                        for j in range(max(0, i-3), i):
                                            prev_word = words[j]['text']
                                            clean_prev = prev_word.replace('.', '').replace(',', '.')
                                            if re.match(r'^\d+[\.,]?\d*$', clean_prev):
                                                # Controllo anti-fasce: evita se F1, F2 o F3 sono vicini
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
                                    # Seleziona prioritariamente il carattere più grande (solitamente il totale in evidenza)
                                    candidates.sort(key=lambda x: x['size'], reverse=True)
                                    consumo = candidates[0]['valore']
                                else:
                                    # Fallback di sicurezza basato su regex se il layout grafico non è standard
                                    match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                                    if match_kwh:
                                        consumo = match_kwh.group(1)
                            except Exception:
                                match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                                if match_kwh:
                                    consumo = match_kwh.group(1)

                            if consumo != "Non rilevato":
                                row_idx = ws.max_row + 1
                                ws.append([azienda, filename, rel_path, consumo])
                                
                                cell = ws.cell(row=row_idx, column=2)
                                cell.hyperlink = abs_path
                                cell.font = openpyxl.styles.Font(color="0563C1", underline="single")
                                
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

tk.Label(root, text="Cartella Bollette (PDF):", font=("Arial", 10, "bold")).pack(anchor="w", padx=20, pady=(10, 5))

frame_path = tk.Frame(root)
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
