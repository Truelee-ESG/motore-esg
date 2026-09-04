import os
import re
import json
import time
import threading
import queue
import webbrowser
import base64
import io
import pandas as pd
from pypdf import PdfReader
from flask import Flask, request, render_template_string, Response, send_file
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from weasyprint import HTML

app = Flask(__name__)

MESI_ORDINE = {
    'Gennaio': 1, 'Febbraio': 2, 'Marzo': 3, 'Aprile': 4,
    'Maggio': 5, 'Giugno': 6, 'Luglio': 7, 'Agosto': 8,
    'Settembre': 9, 'Ottobre': 10, 'Novembre': 11, 'Dicembre': 12
}

# ==========================================
# 1. RICERCA CARTELLA E ESTRAZIONE SOLO CONSUMO
# ==========================================
def trova_cartella_categoria(percorso_root, categoria):
    percorso_root = percorso_root.strip('"\'').strip()
    if not os.path.exists(percorso_root):
        return percorso_root
        
    candidata = percorso_root
    for root, dirs, files in os.walk(percorso_root):
        for directory in dirs:
            nome_dir = directory.lower()
            if categoria == 'energia_elettrica':
                if any(k in nome_dir for k in ["energia", "elettric", "e.e", "luce"]):
                    return os.path.join(root, directory)
            elif categoria == 'trasporti':
                if any(k in nome_dir for k in ["trasporti", "gasolio", "benzina", "carburante", "auto", "furgoni", "mezzi"]):
                    return os.path.join(root, directory)
    return candidata

def estrai_dati_locale(percorso_file, categoria, q):
    nome_file = os.path.basename(percorso_file)
    q.put(f" -> Ricerca esclusiva consumo: {nome_file}")
    
    testo_prima_pagina = ""
    testo_completo = ""
    try:
        reader = PdfReader(percorso_file)
        if len(reader.pages) > 0:
            testo_prima_pagina = reader.pages[0].extract_text() or ""
        for page in reader.pages:
            estratto = page.extract_text()
            if estratto:
                testo_completo += estratto + "\n"
    except Exception:
        q.put(f"   [Errore Lettura] Impossibile aprire il file {nome_file}.")
        return None
        
    testo_p1_lower = testo_prima_pagina.lower()
    testo_tot_lower = testo_completo.lower()
    
    # 1. Trova Mese e Anno
    mesi = ['gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno', 
            'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre']
    mese_trovato = "Gennaio"
    anno_trovato = 2026
    
    for m in mesi:
        if m in nome_file.lower():
            mese_trovato = m.capitalize()
            break
            
    match_anno = re.search(r"(202\d)", nome_file)
    if match_anno:
        anno_trovato = int(match_anno.group(1))
        
    if mese_trovato == "Gennaio":
        for m in mesi:
            if m in testo_p1_lower:
                mese_trovato = m.capitalize()
                break
    if not match_anno:
        match_anno_testo = re.search(r"(202\d)", testo_p1_lower)
        if match_anno_testo:
            anno_trovato = int(match_anno_testo.group(1))

    # 2. Estrazione STRETTAMENTE MIRATA al Consumo/Quantità sulla prima pagina
    quantita = 0.0
    unita_misura = ""

    if categoria == 'energia_elettrica':
        unita_misura = "kWh"
        patterns = [
            r"(?:consumo|energia attiva|prelevata|totale\s*kwh|kwh\s*totali|attiva)[\s\S]{0,30}?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\s*kwh"
        ]
    elif categoria == 'trasporti':
        unita_misura = "Litri"
        patterns = [
            r"(?:quantit[aà]|q\.t[aà]|litri|volume|erogata|totale\s*litri)[\s\S]{0,30}?(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)",
            r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)\s*(?:litri|lt|l)"
        ]
    else:
        patterns = [r"(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d+)?)"]

    for pat in patterns:
        matches = re.findall(pat, testo_p1_lower)
        if matches:
            for val_str in matches:
                if isinstance(val_str, tuple):
                    val_str = val_str[0]
                # Pulizia formato numerico (es. 4.829 o 4.829,00 -> 4829.0)
                val_clean = val_str.replace('.', '').replace(',', '.')
                try:
                    num = float(val_clean)
                    # Scartiamo numeri troppo piccoli o codici isolati, puntando ai consumi reali
                    if num > 5:
                        quantita = num
                        break
                except:
                    continue
            if quantita > 0:
                break

    # 3. Specifico per Trasporti: Rilevamento Gasolio vs Benzina
    tipo_carburante = ""
    if categoria == 'trasporti':
        if any(w in testo_tot_lower for w in ['gasolio', 'diesel', 'f.o.', 'gas.']):
            tipo_carburante = "Gasolio"
        elif any(w in testo_tot_lower for w in ['benzina', 'verde', 'super']):
            tipo_carburante = "Benzina"
        else:
            tipo_carburante = "Non specificato"

    q.put(f"   [OK] Consumo estratto: {quantita} {unita_misura}" + (f" ({tipo_carburante})" if tipo_carburante else ""))
    
    risultato = {
        "Mese": mese_trovato,
        "Anno": anno_trovato,
        "Quantita": quantita,
        "Unita_Misura": unita_misura,
        "File": nome_file
    }
    if categoria == 'trasporti':
        risultato["Tipo_Carburante"] = tipo_carburante
    return risultato

# ==========================================
# 2. GENERAZIONE GRAFICI MATPLOTLIB (BASE64)
# ==========================================
def genera_grafico_base64(df, titolo, ylabel):
    if df.empty:
        return None
    
    plt.figure(figsize=(8, 4.5), dpi=100)
    plt.plot(df['Etichetta'], df['Quantita'], marker='o', color='#2e7d32', linewidth=2.5, markersize=6)
    plt.title(titolo, fontsize=12, fontweight='bold', pad=15)
    plt.xlabel('Periodo (Mese/Anno)', fontsize=10)
    plt.ylabel(ylabel, fontsize=10)
    plt.xticks(rotation=45, ha='right', fontsize=9)
    plt.grid(True, linestyle='--', alpha=0.6)
    plt.tight_layout()
    
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    plt.close()
    return image_base64

# ==========================================
# 3. INTERFACCIA WEB (DASHBOARD)
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Dashboard ESG & Report Dinamici</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 30px; color: #333;}
        .container { max-width: 950px; margin: 0 auto; }
        .box { background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 25px; }
        input { width: 100%; padding: 12px; margin: 8px 0 20px 0; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
        .card { background: #fafbfc; padding: 25px; border-radius: 8px; border: 1px solid #e1e4e8; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.03); }
        .card h3 { margin-top: 0; color: #2e7d32; }
        .btn { padding: 12px 20px; background: #2e7d32; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 1em; width: 100%; margin-top: 15px; transition: background 0.3s; }
        .btn:hover { background: #1b5e20; }
        .btn-report { background: #1a73e8; }
        .btn-report:hover { background: #1557b0; }
        h2 { text-align: center; color: #2e7d32; margin-top: 0; }
        label { font-weight: bold; font-size: 0.9em; color: #333; display: block; text-align: left; }
        hr { border: 0; border-top: 1px solid #eee; margin: 25px 0; }
    </style>
    <script>
        function avviaEstrazione(categoria) {
            const cliente = document.getElementById('nome_cliente').value.trim();
            const percorso = document.getElementById('percorso_root').value.trim();
            if(!cliente || !percorso) { alert("Inserisci prima il Nome Azienda/Cliente e il Percorso!"); return; }
            document.getElementById('form_cliente').value = cliente;
            document.getElementById('form_percorso').value = percorso;
            document.getElementById('form_categoria').value = categoria;
            document.getElementById('hiddenForm').action = "/avvia";
            document.getElementById('hiddenForm').submit();
        }

        function generaReport() {
            const cliente = document.getElementById('nome_cliente').value.trim();
            if(!cliente) { alert("Inserisci il Nome Azienda/Cliente per generare il report!"); return; }
            document.getElementById('report_cliente').value = cliente;
            document.getElementById('reportForm').submit();
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="box">
            <h2>Motore ESG <br><small style="font-size: 0.5em; color: #666;">Estrazione Esclusiva Consumi & Reportistica</small></h2>
            
            <label>Nome Azienda/Cliente (senza spazi):</label>
            <input type="text" id="nome_cliente" placeholder="es. ditta_rossi" required>
            
            <label>Percorso Server/Cartella Principale (Root):</label>
            <input type="text" id="percorso_root" placeholder="es. C:\\Archivio_Dati" required>

            <div class="grid">
                <div class="card">
                    <h3>Ambiente - Energia Elettrica</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai solo i kWh di consumo.</p>
                    <button class="btn" onclick="avviaEstrazione('energia_elettrica')">Estrai Energia Elettrica</button>
                </div>
                
                <div class="card">
                    <h3>Ambiente - Trasporti</h3>
                    <p style="color: #666; font-size: 0.9em;">Estrai solo i litri di carburante.</p>
                    <button class="btn" onclick="avviaEstrazione('trasporti')">Estrai Trasporti</button>
                </div>
            </div>

            <hr>

            <div style="text-align: center;">
                <h3 style="color: #1a73e8; margin-bottom: 5px;">Report & Grafici Dinamici</h3>
                <p style="color: #666; font-size: 0.9em; margin-top:0;">Crea il report di confronto (Mese precedente) ed esportalo in PDF.</p>
                <button class="btn btn-report" style="max-width: 350px;" onclick="generaReport()">Genera Report PDF con Grafici</button>
            </div>
        </div>

        <form id="hiddenForm" action="/avvia" method="POST" style="display: none;">
            <input type="hidden" name="nome_cliente" id="form_cliente">
            <input type="hidden" name="percorso_root" id="form_percorso">
            <input type="hidden" name="categoria" id="form_categoria">
        </form>

        <form id="reportForm" action="/genera_report_pdf" method="POST" style="display: none;">
            <input type="hidden" name="nome_cliente" id="report_cliente">
        </form>
    </div>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_PAGE)

@app.route('/avvia', methods=['POST'])
def avvia_processo():
    nome_cliente = request.form['nome_cliente'].strip()
    percorso_root = request.form['percorso_root'].strip()
    categoria = request.form['categoria'].strip()
    q = queue.Queue()

    def background_worker():
        try:
            if not os.path.exists(percorso_root):
                q.put(f"ERRORE: La cartella non esiste: {percorso_root}")
                q.put(("DONE", None))
                return

            q.put(f"Inizializzazione estrazione consumi per: {categoria.replace('_', ' ').upper()}")
            cartella_target = trova_cartella_categoria(percorso_root, categoria)
            q.put(f"Cartella analizzata: {cartella_target}")

            file_paths = []
            if os.path.exists(cartella_target):
                for f in os.listdir(cartella_target):
                    if f.lower().endswith('.pdf'):
                        file_paths.append(os.path.join(cartella_target, f))

            if not file_paths:
                q.put(f"ATTENZIONE: Nessun file PDF trovato in {cartella_target}.")
                q.put(("DONE", None))
                return

            q.put(f"Trovati {len(file_paths)} file PDF. Estrazione consumi in corso...")

            dati_estratti = []
            for p in file_paths:
                res = estrai_dati_locale(p, categoria, q)
                if res:
                    dati_estratti.append(res)

            q.put("\n--- Generazione file Excel sul Desktop ---")
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            nome_file_excel = os.path.join(desktop_path, f"Report_{categoria.capitalize()}_{nome_cliente}.xlsx")

            with pd.ExcelWriter(nome_file_excel, engine='openpyxl') as writer:
                if dati_estratti:
                    pd.DataFrame(dati_estratti).to_excel(writer, sheet_name=categoria.capitalize(), index=False)
                else:
                    pd.DataFrame([{"Note": "Nessun dato estratto"}]).to_excel(writer, sheet_name='Vuoto', index=False)

            q.put(f"SUCCESSO: File Excel pronto!")
            q.put(("DONE", nome_file_excel))
        except Exception as err:
            import traceback
            q.put(f"ERRORE CRITICO:\n{traceback.format_exc()}")
            q.put(("DONE", None))

    threading.Thread(target=background_worker).start()

    def generate():
        yield f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Elaborazione Consumi ESG</title>
            <style>
                body {{ font-family: Arial; padding: 40px; background: #f4f6f8; }}
                .box {{ background: white; padding: 25px; border-radius: 8px; max-width: 800px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); margin: 0 auto; }}
                #log-box {{ background: #1e1e1e; color: #00ff66; padding: 15px; border-radius: 5px; height: 350px; overflow-y: scroll; font-family: monospace; font-size: 0.9em; margin-top: 15px; white-space: pre-wrap; }}
                h2 {{ color: #2e7d32; text-align: center; margin-top: 0; }}
                .btn {{ display: inline-block; margin-top: 20px; background: #2e7d32; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; font-weight: bold; }}
            </style>
            <script>
                function appendLog(text) {{
                    const box = document.getElementById('log-box');
                    box.innerHTML += text + "\\n";
                    box.scrollTop = box.scrollHeight;
                }}
            </script>
        </head>
        <body>
            <div class="box">
                <h2>Estrazione Consumi {categoria.replace('_', ' ').title()}</h2>
                <div id="log-box"></div>
                <div id="result-area"></div>
            </div>
        </body>
        </html>
        """
        
        while True:
            item = q.get()
            if isinstance(item, tuple) and item[0] == "DONE":
                file_path = item[1]
                if file_path:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #2e7d32; text-align:center;\">Processo Completato!</h3><p style=\"text-align:center;\">File salvato sul Desktop:<br><b>{file_path}</b></p><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                else:
                    yield f"<script>document.getElementById('result-area').innerHTML = '<h3 style=\"color: #c62828; text-align:center;\">Terminato con errori.</h3><div style=\"text-align:center;\"><a href=\"/\" class=\"btn\">Torna alla Home</a></div>';</script>"
                break
            else:
                safe_msg = str(item).replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n')
                yield f"<script>appendLog('{safe_msg}');</script>\n"

    return Response(generate(), mimetype='text/html')

@app.route('/genera_report_pdf', methods=['POST'])
def genera_report_pdf():
    nome_cliente = request.form['nome_cliente'].strip()
    desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
    
    file_ee = os.path.join(desktop_path, f"Report_Energia_elettrica_{nome_cliente}.xlsx")
    file_tr = os.path.join(desktop_path, f"Report_Trasporti_{nome_cliente}.xlsx")
    
    sezioni_html = ""
    
    if os.path.exists(file_ee):
        df_ee = pd.read_excel(file_ee)
        if not df_ee.empty and 'Quantita' in df_ee.columns:
            df_ee['NumMese'] = df_ee['Mese'].map(MESI_ORDINE).fillna(1)
            df_ee = df_ee.sort_values(by=['Anno', 'NumMese'])
            df_ee['Etichetta'] = df_ee['Mese'] + ' ' + df_ee['Anno'].astype(str)
            
            df_ee['Var_MoM'] = df_ee['Quantita'].diff()
            df_ee['Var_MoM_%'] = df_ee['Quantita'].pct_change() * 100
            
            grafico_ee = genera_grafico_base64(df_ee, 'Andamento Consumi - Energia Elettrica', 'kWh')
            
            table_html = df_ee[['Etichetta', 'Quantita', 'Unita_Misura', 'Var_MoM', 'Var_MoM_%']].to_html(index=False, classes='table-report', float_format=lambda x: f"{x:.2f}" if pd.notnull(x) else "-")
            
            sezioni_html += f"""
            <h2>Report Energia Elettrica</h2>
            <p>Cliente: <b>{nome_cliente}</b></p>
            {f'<div style="text-align:center; margin: 20px 0;"><img src="data:image/png;base64,{grafico_ee}" style="max-width:100%; border-radius:6px; border:1px solid #ddd;"></div>' if grafico_ee else ''}
            <h3>Tabella Consumi & Variazioni</h3>
            {table_html}
            <div class="page-break"></div>
            """

    if os.path.exists(file_tr):
        df_tr = pd.read_excel(file_tr)
        if not df_tr.empty and 'Quantita' in df_tr.columns:
            df_tr['NumMese'] = df_tr['Mese'].map(MESI_ORDINE).fillna(1)
            df_tr = df_tr.sort_values(by=['Anno', 'NumMese'])
            df_tr['Etichetta'] = df_tr['Mese'] + ' ' + df_tr['Anno'].astype(str)
            
            df_tr['Var_MoM'] = df_tr['Quantita'].diff()
            df_tr['Var_MoM_%'] = df_tr['Quantita'].pct_change() * 100
            
            grafico_tr = genera_grafico_base64(df_tr, 'Andamento Consumi - Trasporti (Carburante)', 'Litri')
            
            table_html_tr = df_tr[['Etichetta', 'Quantita', 'Unita_Misura', 'Tipo_Carburante', 'Var_MoM', 'Var_MoM_%']].to_html(index=False, classes='table-report', float_format=lambda x: f"{x:.2f}" if pd.notnull(x) else "-")
            
            sezioni_html += f"""
            <h2>Report Trasporti & Carburanti</h2>
            <p>Cliente: <b>{nome_cliente}</b></p>
            {f'<div style="text-align:center; margin: 20px 0;"><img src="data:image/png;base64,{grafico_tr}" style="max-width:100%; border-radius:6px; border:1px solid #ddd;"></div>' if grafico_tr else ''}
            <h3>Tabella Consumi & Variazioni</h3>
            {table_html_tr}
            """

    if not sezioni_html:
        sezioni_html = "<h3>Nessun dato trovato</h3><p>Esegui prima l'estrazione dei consumi per questo cliente.</p>"

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            @page {{ size: A4; margin: 20mm; @bottom-right {{ content: counter(page); }} }}
            body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; color: #333; line-height: 1.5; font-size: 11pt; }}
            h1 {{ color: #2e7d32; border-bottom: 2px solid #2e7d32; padding-bottom: 8px; margin-bottom: 5px; }}
            h2 {{ color: #1a73e8; margin-top: 30px; border-bottom: 1px solid #ddd; padding-bottom: 5px; }}
            h3 {{ color: #444; margin-top: 20px; }}
            .subtitle {{ color: #666; font-size: 10pt; margin-bottom: 30px; }}
            .table-report {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 9.5pt; }}
            .table-report th, .table-report td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: center; }}
            .table-report th {{ background-color: #f2f2f2; color: #333; font-weight: bold; }}
            .table-report tr:nth-child(even) {{ background-color: #fafafa; }}
            .page-break {{ page-break-after: always; }}
        </style>
    </head>
    <body>
        <h1>Report di Sostenibilità & Consumi ESG</h1>
        <div class="subtitle">Analisi esclusiva consumi per <b>{nome_cliente}</b></div>
        {sezioni_html}
    </body>
    </html>
    """

    pdf_filename = os.path.join(desktop_path, f"Report_ESG_{nome_cliente}.pdf")
    try:
        HTML(string=html_content).write_pdf(pdf_filename)
        return send_file(pdf_filename, as_attachment=True)
    except Exception as e:
        return f"<h3>Errore generazione PDF:</h3><p>{str(e)}</p>", 500

if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(debug=False, port=5000)
