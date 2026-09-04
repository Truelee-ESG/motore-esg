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

    # Ricerca ricorsiva in tutte le sottocartelle
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
    
    ws.append(["Azienda", "Nome File", "Percorso", "Consumo Stimato (kWh)"])
    
    success_count = 0
    total_files = len(pdf_files)
    
    for root_dir, filename in pdf_files:
        file_path = os.path.join(root_dir, filename)
        rel_path = os.path.relpath(file_path, cartella)
        consumo = "Non rilevato"
        
        try:
            with pdfplumber.open(file_path) as pdf:
                if len(pdf.pages) > 0:
                    text = pdf.pages[0].extract_text()
                    if text:
                        match_kwh = re.search(r'(\d+[\.,]?\d*)\s*(?:kWh|KWh|KWH)', text)
                        if match_kwmatch := match_kwh:
                            consumo = match_kwmatch.group(1)
        except Exception as e:
            consumo = f"Errore: {str(e)}"
            
        ws.append([azienda, filename, rel_path, consumo])
        success_count += 1
        status_label.config(text=f"Elaborato ({success_count}/{total_files}): {filename}")
        status_label.update()

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
