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

def scansiona_file(cartella, tipo_modulo):
    valid_extensions = ('.pdf', '.jpg', '.jpeg', '.png')
    
    if tipo_modulo == "elettrica":
        bill_keywords = ('boll', 'fatt', 'ener', 'luce', 'bill', 'invo', 'elett', 'consum', 'pod', 'fornit')
    else:  # trasporti
        bill_keywords = ('fatt', 'carbur', 'benzina', 'gasolio', 'diesel', 'fuel', 'petrol', 'distrib', 'scheda')

    file_list = []
    for root_dir, _, files in os.walk(cartella):
        for filename in files:
            if filename.lower().endswith(valid_extensions):
                filename_lower = filename.lower()
                if any(kw in filename_lower for kw in bill_keywords):
                    file_list.append((root_dir, filename))
    return file_list

def estrai_dati_comuni(text, text_lower, mesi_mappa):
    anno = "Non rilevato"
    years = re.findall(r'\b(20\d{2})\b', text)
    if years:
        anno = years[0]
        
    periodo = "Non rilevato"
    found_mesi = []
    for m_key, m_val in mesi_mappa.items():
        if re.search(r'\b' + m_key + r'\b', text_lower):
            if m_val not in found_mesi:
                found_mesi.append(m_val)
    if found_mesi:
        periodo = found_mesi[0] if len(found_mesi) == 1 else f"{found_mesi[0]} - {found_mesi[-1]}"
        
    return periodo, anno

def analizza_energia(azienda, cartella, status_label):
    file_list = scansiona_file(cartella, "elettrica")
    if not file_list:
        messagebox.showwarning("Attenzione", "Nessun file mirato per Energia Elettrica trovato nella cartella.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Consumi Elettrici"
    
    current_time_str = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    ws.append([f"Estrazione dati bollette energia elettrica in data {current_time_str}"])
    ws.cell(row=1, column=1).font = Font(size=12, bold=True, color="2E7D32")
    ws.append([])
    
    headers = ["Azienda", "Nome File", "Periodo", "Anno", "Consumo", "Unità di misura"]
    ws.append(headers)
    
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=3, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    mesi_mappa = {
        'gennaio': 'Gennaio', 'febbraio': 'Febbraio', 'marzo': 'Marzo', 'aprile': 'Aprile',
        'maggio': 'Maggio', 'giugno': 'Giugno', 'luglio': 'Luglio', 'agosto': 'Agosto',
        'settembre': 'Settembre', 'ottobre': 'Ottobre', 'novembre': 'Novembre', 'dicembre': 'Dicembre',
        'gen': 'Gen', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr', 'mag': 'Mag', 'giu': 'Giu',
        'lug': 'Lug', 'ago': 'Ago', 'set': 'Set', 'ott': 'Ott', 'nov': 'Nov', 'dic': 'Dic'
    }

    success_count, valid_count, total_files = 0, 0, len(file_list)
    
    for root_dir, filename in file_list:
        file_path = os.path.join(root_dir, filename)
        abs_path = os.path.abspath(file_path)
        ext = filename.lower()
        
        consumo, unita_misura = "Non rilevato", "kWh"
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
                                periodo, anno = estrai_dati_comuni(text, text_lower, mesi_mappa)
                                try:
                                    words = page.extract_words(extra_attrs=["size"])
                                    candidates = []
                                    for i, word in enumerate(words):
                                        if re.match(r'^(?:kWh|KWh|KWH)$', word['text']):
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
                                                        candidates.append({'valore': prev_word, 'size': words[j].get('size', 0)})
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
                text = pytesseract.image_to_string(Image.open(file_path))
                if text:
                    text_lower = text.lower()
                    if "energia elettrica" in text_lower or "kwh" in text_lower:
                        periodo, anno = estrai_dati_comuni(text, text_lower, mesi_mappa)
                        match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                        if match_kwh:
                            consumo = match_kwh.group(1)

            if consumo != "Non rilevato":
                row_idx = ws.max_row + 1
                ws.append([azienda, filename, periodo, anno, consumo, unita_misura])
                cell_file = ws.cell(row=row_idx, column=2)
                cell_file.hyperlink = abs_path
                cell_file.font = Font(color="0563C1", underline="single")
                valid_count += 1
        except Exception:
            pass
            
        success_count += 1
        status_label.config(text=f"Energia - Analizzati ({success_count}/{total_files}) | Trovati: {valid_count}")
        status_label.update()

    if valid_count == 0:
        messagebox.showwarning("Attenzione", "Nessun dato di energia elettrica valido estratto.")
        return

    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_path = os.path.join(desktop_path, f"Energia_Elettrica_{azienda.replace(' ', '_')}.xlsx")
    wb.save(output_path)
    status_label.config(text="Completato Energia Elettrica!")
    messagebox.showinfo("Successo", f"File Excel salvato sul Desktop:\n{os.path.basename(output_path)}")

def analizza_trasporti(azienda, cartella, status_label):
    file_list = scansiona_file(cartella, "trasporti")
    if not file_list:
        messagebox.showwarning("Attenzione", "Nessun file mirato per Trasporti trovato nella cartella.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Trasporti Carburante"
    
    current_time_str = datetime.now().strftime("%d/%m/%Y alle %H:%M")
    ws.append([f"Estrazione dati fatture carburante in data {current_time_str}"])
    ws.cell(row=1, column=1).font = Font(size=12, bold=True, color="2E7D32")
    ws.append([])
    
    headers = ["Azienda", "Nome File", "Periodo", "Anno", "Consumo", "Unità di misura"]
    ws.append(headers)
    
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=3, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="2E7D32", end_color="2E7D32", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    mesi_mappa = {
        'gennaio': 'Gennaio', 'febbraio': 'Febbraio', 'marzo': 'Marzo', 'aprile': 'Aprile',
        'maggio': 'Maggio', 'giugno': 'Giugno', 'luglio': 'Luglio', 'agosto': 'Agosto',
        'settembre': 'Settembre', 'ottobre': 'Ottobre', 'novembre': 'Novembre', 'dicembre': 'Dicembre',
        'gen': 'Gen', 'feb': 'Feb', 'mar': 'Mar', 'apr': 'Apr', 'mag': 'Mag', 'giu': 'Giu',
        'lug': 'Lug', 'ago': 'Ago', 'set': 'Set', 'ott': 'Ott', 'nov': 'Nov', 'dic': 'Dic'
    }

    success_count, valid_count, total_files = 0, 0, len(file_list)
    
    for root_dir, filename in file_list:
        file_path = os.path.join(root_dir, filename)
        abs_path = os.path.abspath(file_path)
        ext = filename.lower()
        
        quantita = "Non rilevato"
        unita_misura = "Litri"
        text = ""
        try:
            if ext.endswith('.pdf'):
                with pdfplumber.open(file_path) as pdf:
                    if len(pdf.pages) > 0:
                        page = pdf.pages[0]
                        text = page.extract_text()
            elif ext.endswith(('.jpg', '.jpeg', '.png')):
                text = pytesseract.image_to_string(Image.open(file_path))
                
            if text:
                text_lower = text.lower()
                # Verifica se contiene termini correlati al carburante
                if any(w in text_lower for w in ["gasolio", "benzina", "diesel", "litri", "carburante", "l."]):
                    periodo, anno = estrai_dati_comuni(text, text_lower, mesi_mappa)
                    
                    # Rilevamento tipo carburante specifico per l'unità o annotazione
                    if "benzina" in text_lower:
                        unita_misura = "Litri (Benzina)"
                    elif "gasolio" in text_lower or "diesel" in text_lower:
                        unita_misura = "Litri (Gasolio)"
                        
                    # Cerca pattern dei litri (es. 45,50 litri oppure quantità seguita da L o litri)
                    match_litri = re.search(r'(\d+[\.,]?\d*)\s*(?:litri|Litri|LITRI|\blit\b|\bl\b)', text)
                    if match_litri:
                        quantita = match_litri.group(1)
                    else:
                        # Fallback: cerca numeri vicini a parole chiave carburante
                        match_num = re.search(r'(?:quantit[àa]|q\.tà|litri)\D{0,10}(\d+[\.,]?\d*)', text_lower)
                        if match_num:
                            quantita = match_num.group(1)

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
        status_label.config(text=f"Trasporti - Analizzati ({success_count}/{total_files}) | Trovati: {valid_count}")
        status_label.update()

    if valid_count == 0:
        messagebox.showwarning("Attenzione", "Nessun dato di carburante/trasporti valido estratto.")
        return

    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    output_path = os.path.join(desktop_path, f"Trasporti_{azienda.replace(' ', '_')}.xlsx")
    wb.save(output_path)
    status_label.config(text="Completato Trasporti!")
    messagebox.showinfo("Successo", f"File Excel salvato sul Desktop:\n{os.path.basename(output_path)}")

def seleziona_cartella():
    path = filedialog.askdirectory()
    if path:
        entry_path.delete(0, tk.END)
        entry_path.insert(0, path)

def avvia_energia_gui():
    analizza_energia(entry_azienda.get().strip(), entry_path.get().strip(), lbl_status)

def avvia_trasporti_gui():
    analizza_trasporti(entry_azienda.get().strip(), entry_path.get().strip(), lbl_status)

# --- COSTRUZIONE INTERFACCIA GRAFICA PREMIUM (Basata sul layout richiesto) ---
root = tk.Tk()
root.title("Motore ESG - Estrazione Consumi Rapida")
root.geometry("640x560")
root.resizable(False, False)
root.configure(bg="#f1f5f9")

FONT_FAMILY = "Segoe UI"

# Contenitore principale stile card bianca
main_card = tk.Frame(root, bg="#ffffff", relief="solid", bd=1)
main_card.place(x=20, y=20, width=600, height=520)

# Titoli superiori
lbl_title = tk.Label(main_card, text="Motore ESG", font=(FONT_FAMILY, 18, "bold"), bg="#ffffff", fg="#1b5e20")
lbl_title.pack(pady=(25, 2))

lbl_subtitle = tk.Label(main_card, text="Estrazione Consumi Rapida", font=(FONT_FAMILY, 10), bg="#ffffff", fg="#64748b")
lbl_subtitle.pack(pady=(0, 15))

# Campo Azienda
frame_az = tk.Frame(main_card, bg="#ffffff")
frame_az.pack(fill="x", padx=35, pady=6)
tk.Label(frame_az, text="Nome Azienda/Cliente (senza spazi):", font=(FONT_FAMILY, 10, "bold"), bg="#ffffff", fg="#334155").pack(anchor="w", pady=(0, 4))
entry_azienda = tk.Entry(frame_az, font=(FONT_FAMILY, 10), relief="solid", bd=1)
entry_azienda.pack(fill="x", ipady=5)

# Campo Percorso Cartella & Sfoglia
frame_p = tk.Frame(main_card, bg="#ffffff")
frame_p.pack(fill="x", padx=35, pady=6)
tk.Label(frame_p, text="Percorso Server/Cartella Principale (Root):", font=(FONT_FAMILY, 10, "bold"), bg="#ffffff", fg="#334155").pack(anchor="w", pady=(0, 4))

sub_p = tk.Frame(frame_p, bg="#ffffff")
sub_p.pack(fill="x")
entry_path = tk.Entry(sub_p, font=(FONT_FAMILY, 10), relief="solid", bd=1)
entry_path.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
btn_browse = tk.Button(sub_p, text="Sfoglia...", command=seleziona_cartella, font=(FONT_FAMILY, 9, "bold"), bg="#e2e8f0", fg="#334155", relief="flat", cursor="hand2", padx=12, pady=5)
btn_browse.pack(side="right")

# Sezione Box Duali (Energia Elettrica vs Trasporti)
boxes_frame = tk.Frame(main_card, bg="#ffffff")
boxes_frame.pack(fill="x", padx=35, pady=15)

# Box Sinistro: Energia Elettrica
box_elec = tk.Frame(boxes_frame, bg="#ffffff", relief="solid", bd=1, highlightbackground="#cbd5e1")
box_elec.pack(side="left", fill="both", expand=True, padx=(0, 8), ipady=10)

tk.Label(box_elec, text="Ambiente - Energia Elettrica", font=(FONT_FAMILY, 11, "bold"), bg="#ffffff", fg="#1b5e20").pack(pady=(12, 4))
tk.Label(box_elec, text="Estrai kWh da Prima Pagina\ncon Link diretti.", font=(FONT_FAMILY, 9), bg="#ffffff", fg="#64748b", justify="center").pack(pady=(0, 12))
btn_run_elec = tk.Button(box_elec, text="Avvia Energia Elettrica", command=avvia_energia_gui, bg="#2e7d32", fg="white", font=(FONT_FAMILY, 10, "bold"), relief="flat", cursor="hand2", padx=10, pady=8)
btn_run_elec.pack(padx=15, fill="x")

# Box Destro: Trasporti
box_trans = tk.Frame(boxes_frame, bg="#ffffff", relief="solid", bd=1, highlightbackground="#cbd5e1")
box_trans.pack(side="right", fill="both", expand=True, padx=(8, 0), ipady=10)

tk.Label(box_trans, text="Ambiente - Trasporti", font=(FONT_FAMILY, 11, "bold"), bg="#ffffff", fg="#1b5e20").pack(pady=(12, 4))
tk.Label(box_trans, text="Estrai litri e tipo carburante\ndalle fatture.", font=(FONT_FAMILY, 9), bg="#ffffff", fg="#64748b", justify="center").pack(pady=(0, 12))
btn_run_trans = tk.Button(box_trans, text="Avvia Trasporti", command=avvia_trasporti_gui, bg="#2e7d32", fg="white", font=(FONT_FAMILY, 10, "bold"), relief="flat", cursor="hand2", padx=10, pady=8)
btn_run_trans.pack(padx=15, fill="x")

# Barra di stato in basso
lbl_status = tk.Label(main_card, text="Pronto per l'estrazione ESG.", font=(FONT_FAMILY, 9, "italic"), bg="#ffffff", fg="#64748b")
lbl_status.pack(pady=(5, 10))

root.mainloop()
