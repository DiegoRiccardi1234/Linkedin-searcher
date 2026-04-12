# ============================================================
# 🤖 Job Analyzer Bot v4.0 - Diego Riccardi
# ============================================================
# pip install python-jobspy groq pandas colorama
# ============================================================

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
import pandas as pd
from datetime import datetime
from jobspy import scrape_jobs
from groq import Groq

# Forza UTF-8 su stdout/stderr (fix emoji su Windows/cp1252)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
from colorama import Fore, Style, init

init(autoreset=True)

# ──────────────────────────────────────────────────────────────
# ⚙️  CONFIGURAZIONE
# ──────────────────────────────────────────────────────────────

_key_file    = Path(__file__).parent / "groq key.txt"
GROQ_API_KEY = _key_file.read_text(encoding="utf-8").strip()  # 👉 console.groq.com
GROQ_MODEL   = "meta-llama/llama-4-maverick-17b-128e-instruct"     # 500k TPD vs 100k del 70b

RICERCHE = [
    # ── Analisi e Processi ──────────────────────────
    "Analista Funzionale Junior",
    "Junior Business Analyst IT",
    "Junior Data Analyst",
    "Junior Process Analyst",
    "Junior Product Analyst",
    "Junior Requirements Analyst",
    "Stage Analista Funzionale",

    # ── Testing e QA ────────────────────────────────
    "Junior QA Tester",
    "Junior Test Analyst",
    "Junior Software Tester",
    "Junior Quality Assurance",
    "Junior Automation Tester",

    # ── Cybersecurity (Master) ───────────────────────
    "Junior Cybersecurity Analyst",
    "Junior SOC Analyst",
    "Junior Information Security Analyst",
    "Junior GRC Analyst",
    "Junior IT Auditor",
    "Junior Penetration Tester",
    "Junior Vulnerability Analyst",

    # ── Cloud e Infrastruttura ───────────────────────
    "Junior System Administrator",
    "Junior Cloud Support Engineer",
    "Junior IT Operations",
    "Junior Infrastructure Analyst",
    "Junior DevOps Engineer",

    # ── AI e Automazione ────────────────────────────
    "Junior RPA Developer",
    "Junior Automation Specialist",
    "No Code Developer Junior",
    "Junior AI Integration Specialist",
    "Prompt Engineer Junior",

    # ── Consulenza Piattaforme ───────────────────────
    "Junior Salesforce Administrator",
    "Junior ServiceNow",
    "Junior SAP Consultant",
    "Junior Dynamics 365",

    # ── Consulenza IT Generica ───────────────────────
    "Junior IT Consultant",
    "Junior IT Analyst",
    "Junior Technical Analyst",
    "Junior Management Consultant IT",

    # ── Support e Operations ─────────────────────────
    "Junior IT Support",
    "Junior Help Desk",
    "Junior Application Support",
    "Junior Service Desk",

    # ── Gaming (passione) ────────────────────────────
    "Junior Game QA Tester",
    "Junior Community Manager Gaming",
    "Junior Product Manager Gaming",

    # ── Vibecoding e AI Tools (superpotere di Diego) ─
    "AI Automation Specialist Junior",
    "Junior AI Consultant",
    "Junior Generative AI Specialist",
    "Junior LLM Application Developer",
    "AI Tools Developer Junior",
    "Junior Make Automation Specialist",
    "Junior n8n Developer",
    "Junior AI Product Builder",

    # ── Stage e Apprendistato generici ───────────────
    "Stage Informatica Torino",
    "Apprendistato Informatica Torino",
    "Tirocinante IT Torino",
    "Neolaureato Informatica Torino",
]

LOCATION            = "Torino, Italy"
MAX_ANNUNCI         = 20          # Per ricerca — 48 ricerche × 20 = ~960 lordi
HOURS_OLD           = 336         # Ultimi 14 giorni
OUTPUT_FILE         = f"lavori_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
DELAY_TRA_CHIAMATE  = 1.5         # Secondi tra chiamate Groq
DELAY_TRA_RICERCHE  = 4.0         # Secondi tra ricerche scraping (evita IP ban)
FILE_IN_CORSO       = Path(__file__).parent / "lavori_IN_CORSO.csv"

LOCATION_REMOTE = "Italy"

RICERCHE_REMOTE = [
    # Ruoli più adatti al full remote — selezionati per alto tasso di remote in IT
    "Junior AI Automation Specialist",
    "Junior AI Consultant",
    "Prompt Engineer Junior",
    "Junior RPA Developer",
    "No Code Developer Junior",
    "Junior Salesforce Administrator",
    "Junior ServiceNow",
    "Analista Funzionale Junior",
    "Junior QA Tester",
    "Junior Cybersecurity Analyst",
    "Junior GRC Analyst",
    "Junior Data Analyst",
    "Junior IT Analyst",
    "Junior Make Automation Specialist",
    "Junior n8n Developer",
]

# Frasi specifiche per evitare falsi positivi
FILTRO_BLACKLIST = [
    "senior developer",
    "senior engineer",
    "senior consultant",
    "senior analyst",
    "senior specialist",
    "lead developer",
    "lead engineer",
    "principal engineer",
    "principal architect",
    "5+ anni",
    "5 anni di esperienza",
    "almeno 5 anni",
    "4+ anni",
    "4 anni di esperienza",
    "almeno 4 anni",
    "partita iva",
    "p.iva",
    "freelance",
    "free-lance",
    "direttore",
    "responsabile di funzione",
    "c-level",
    "cto",
    "ciso",
    "chief",
]

# ──────────────────────────────────────────────────────────────
# Setup client Groq + Llama 3.3 70B
# ──────────────────────────────────────────────────────────────

client = Groq(api_key=GROQ_API_KEY)


# ──────────────────────────────────────────────────────────────
# 🔍 Pre-filtro veloce (senza AI)
# ──────────────────────────────────────────────────────────────

def pre_filtro(titolo: str, descrizione: str) -> tuple[bool, str]:
    testo = (titolo + " " + descrizione).lower()
    for frase in FILTRO_BLACKLIST:
        if frase.lower() in testo:
            return True, f"Blacklist: '{frase}'"
    return False, ""


# ──────────────────────────────────────────────────────────────
# 🤖 Analisi AI con Llama 3.3 70B
# ──────────────────────────────────────────────────────────────

def analizza_lavoro(titolo: str, azienda: str, descrizione: str) -> dict:
    prompt = f"""Analizza questa offerta IT per Diego e rispondi SOLO con JSON valido, nessun testo extra.

CANDIDATO: Diego, 27 anni, Torino. Neolaureato Informatica UniTo (95/110, 2025), Master Cybersecurity in corso. \
Tirocinio Finwave: portale B2B cloud (React, TypeScript, Java Quarkus, PostgreSQL, MongoDB, SSO, RBAC). \
NON vuole programmare molto: preferisce logica, analisi, QA, sicurezza, automazione. \
Cerca dipendente/apprendistato, smart working ibrido/full-remote, NO Partita IVA. \
Punto di forza unico: "vibecoding" — costruisce tool AI complessi con LLM senza scrivere codice manualmente \
(Groq, OpenRouter, n8n, Make.com). Ideale per AI Automation, No-Code, IT Analyst, QA, SOC.

OFFERTA:
Titolo: {titolo} | Azienda: {azienda}
{descrizione[:1200]}

Valuta: programmazione richiesta, junior-friendliness reale, smart working, tipo contratto, RAL range 2026 (junior IT Italia), reputazione "{azienda}" come datore.

JSON (solo questo, nessun testo prima o dopo):
{{"punteggio":<1-10>,"programmazione_richiesta":"Bassa|Media|Alta","smart_working":"Sì|No|Non specificato","contratto":"Dipendente|Apprendistato|Stage|Partita IVA|Non specificato","junior_friendly":"Sì|No","anni_esperienza_richiesti":"0|1|2|3+|Non specificato","punti_forza_per_diego":"1 frase","punti_deboli_per_diego":"1 frase","riassunto":"2 righe max","consiglio":"Candidati subito|Valutabile|Salta","ral_stimata":"XX.000€-YY.000€|Non stimabile","reputazione_azienda":"Ottima|Buona|Nella media|Scarsa|Sconosciuta","adatta_neolaureati":"Sì|No|Non specificato","note_azienda":"1 frase"}}"""

    MAX_RETRY = 3
    last_error: Exception = RuntimeError("Nessun tentativo effettuato")
    for tentativo in range(MAX_RETRY):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_completion_tokens=400,
            )
            testo = (response.choices[0].message.content or "").strip()
            match = re.search(r'\{.*\}', testo, re.DOTALL)
            if match:
                return json.loads(match.group())
            raise ValueError("Nessun JSON trovato nella risposta")

        except Exception as e:
            if tentativo < MAX_RETRY - 1:
                # Parsing del wait time dal messaggio 429 di Groq
                attesa = 65  # fallback
                m = re.search(r'try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s', str(e))
                if m:
                    minuti = int(m.group(1) or 0)
                    secondi = float(m.group(2))
                    attesa = int(minuti * 60 + secondi) + 10  # +10s buffer
                print(f"     {Fore.YELLOW}⚠ Retry {tentativo + 1}/{MAX_RETRY} — rate limit, aspetto {attesa}s...")
                time.sleep(attesa)
            else:
                last_error = e
    return {
        "punteggio": 0,
        "programmazione_richiesta": "?",
        "smart_working": "?",
        "contratto": "?",
        "junior_friendly": "?",
        "anni_esperienza_richiesti": "?",
        "punti_forza_per_diego": "?",
        "punti_deboli_per_diego": "?",
        "riassunto": f"Errore analisi: {last_error}",
        "consiglio": "Da verificare manualmente",
        "ral_stimata": "Non stimabile",
        "reputazione_azienda": "?",
        "adatta_neolaureati": "?",
        "note_azienda": "?",
    }


# ──────────────────────────────────────────────────────────────
# 🎨 Colori console
# ──────────────────────────────────────────────────────────────

def colora_consiglio(consiglio: str) -> str:
    c = consiglio.lower()
    if "subito" in c:    return Fore.GREEN  + consiglio + Style.RESET_ALL
    elif "valutabile" in c: return Fore.YELLOW + consiglio + Style.RESET_ALL
    else:                return Fore.RED    + consiglio + Style.RESET_ALL

def colora_punteggio(p) -> str:
    try:
        n = int(p)
        if n >= 7:   return Fore.GREEN  + f"{n}/10" + Style.RESET_ALL
        elif n >= 5: return Fore.YELLOW + f"{n}/10" + Style.RESET_ALL
        else:        return Fore.RED    + f"{n}/10" + Style.RESET_ALL
    except (ValueError, TypeError):
        return str(p)


# ──────────────────────────────────────────────────────────────
# 💾 Salvataggio incrementale
# ──────────────────────────────────────────────────────────────

COLONNE = [
    "Modalità", "Punteggio AI", "Consiglio", "Titolo", "Azienda", "Sede", "Fonte",
    "Programmazione richiesta", "Smart Working", "Contratto", "Junior Friendly",
    "Anni esperienza richiesti", "Punti forza per Diego", "Punti deboli per Diego",
    "Riassunto AI", "Stipendio Min (jobspy)", "Stipendio Max (jobspy)", "RAL Stimata AI",
    "Reputazione Azienda", "Adatta Neolaureati", "Note Azienda", "Ricerca usata", "Link",
]

def scrivi_riga_csv(riga: dict) -> None:
    """Aggiunge una riga al file IN_CORSO in append mode."""
    file_nuovo = not FILE_IN_CORSO.exists() or FILE_IN_CORSO.stat().st_size == 0
    with open(FILE_IN_CORSO, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLONNE)
        if file_nuovo:
            writer.writeheader()
        writer.writerow(riga)

def formatta_eta(secondi: float) -> str:
    """Converte secondi in stringa leggibile tipo '4m 30s'."""
    s = int(secondi)
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60:02d}s"


# ──────────────────────────────────────────────────────────────
# 🔄 Core: scraping + analisi per un gruppo di ricerche
# ──────────────────────────────────────────────────────────────

def esegui_ricerche(
    ricerche: list,
    location: str,
    modalita: str,
    annunci_gia_visti: set,
    stato: dict,
    is_remote: bool = False,
) -> list[dict]:
    """
    Esegue scraping + pre-filtro + analisi AI per una lista di ricerche.
    Scrive ogni risultato immediatamente su FILE_IN_CORSO.
    Aggiorna il dizionario `stato` con i contatori e la progressione.
    Restituisce la lista di dizionari risultato.
    """
    risultati: list[dict] = []
    annunci_visti_locali: set = set()   # dedup interno a questa sessione di ricerca

    for ricerca in ricerche:
        stato["completate"] += 1
        n   = stato["completate"]
        tot = stato["tot"]

        # Calcolo ETA
        elapsed = time.time() - stato["inizio"]
        if n > 1:
            media   = elapsed / (n - 1)
            eta_str = f" — ETA ~{formatta_eta(media * (tot - n + 1))}"
        else:
            eta_str = ""

        colore = Fore.CYAN if modalita == "Torino" else Fore.MAGENTA
        prefisso = "Remote: " if is_remote else "Cerco: "
        print(f"\n{colore}[{n}/{tot}] 🔍 {prefisso}'{ricerca}' @ {location}{eta_str}")

        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=ricerca,
                location=location,
                is_remote=is_remote,
                results_wanted=MAX_ANNUNCI,
                hours_old=HOURS_OLD,
                country_indeed="Italy",
            )
            time.sleep(DELAY_TRA_RICERCHE)   # delay solo in caso di successo
        except Exception as e:
            print(f"  {Fore.RED}Errore scraping: {e}")
            continue

        jobs = jobs.drop_duplicates(subset=["title", "company"])
        print(f"  Trovati {len(jobs)} annunci unici. Pre-filtro in corso...")
        stato["trovati"] += len(jobs)

        for _, job in jobs.iterrows():
            titolo        = str(job.get("title", "N/A"))
            azienda       = str(job.get("company", "N/A"))
            descrizione   = str(job.get("description", ""))
            link          = str(job.get("job_url", ""))
            sede          = str(job.get("location", ""))
            fonte         = str(job.get("site", ""))
            stipendio_min = job.get("min_amount", None)
            stipendio_max = job.get("max_amount", None)

            chiave = f"{titolo}|{azienda}"

            # Salta duplicati interni alla sessione o già visti in resume
            if chiave in annunci_visti_locali or chiave in annunci_gia_visti:
                continue
            annunci_visti_locali.add(chiave)

            # Pre-filtro senza AI
            scartare, motivo = pre_filtro(titolo, descrizione)
            if scartare:
                print(f"  {Fore.RED}✗ Scartato [{motivo}]: {titolo} @ {azienda}")
                stato["scartati"] += 1
                continue

            # Analisi AI
            print(f"  {Fore.YELLOW}⏳ Analizzo: {titolo} @ {azienda}")
            analisi = analizza_lavoro(titolo, azienda, descrizione)
            stato["analizzati"] += 1

            punteggio = analisi.get("punteggio", 0)
            consiglio = analisi.get("consiglio", "?")
            print(f"     → {colora_punteggio(punteggio)} | {colora_consiglio(consiglio)}")

            riga: dict = {
                "Modalità":                  modalita,
                "Punteggio AI":              punteggio,
                "Consiglio":                 consiglio,
                "Titolo":                    titolo,
                "Azienda":                   azienda,
                "Sede":                      sede,
                "Fonte":                     fonte,
                "Programmazione richiesta":  analisi.get("programmazione_richiesta", "?"),
                "Smart Working":             analisi.get("smart_working", "?"),
                "Contratto":                 analisi.get("contratto", "?"),
                "Junior Friendly":           analisi.get("junior_friendly", "?"),
                "Anni esperienza richiesti": analisi.get("anni_esperienza_richiesti", "?"),
                "Punti forza per Diego":     analisi.get("punti_forza_per_diego", "?"),
                "Punti deboli per Diego":    analisi.get("punti_deboli_per_diego", "?"),
                "Riassunto AI":              analisi.get("riassunto", "?"),
                "Stipendio Min (jobspy)":    stipendio_min if stipendio_min else "N/D",
                "Stipendio Max (jobspy)":    stipendio_max if stipendio_max else "N/D",
                "RAL Stimata AI":            analisi.get("ral_stimata", "Non stimabile"),
                "Reputazione Azienda":       analisi.get("reputazione_azienda", "?"),
                "Adatta Neolaureati":        analisi.get("adatta_neolaureati", "?"),
                "Note Azienda":              analisi.get("note_azienda", "?"),
                "Ricerca usata":             ricerca,
                "Link":                      link,
            }

            risultati.append(riga)
            scrivi_riga_csv(riga)   # salvataggio incrementale immediato

            time.sleep(DELAY_TRA_CHIAMATE)

    return risultati


# ──────────────────────────────────────────────────────────────
# 🚀 Main
# ──────────────────────────────────────────────────────────────

def main() -> None:
    # ── Argparse ──────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="Job Analyzer Bot — Diego Riccardi")
    parser.add_argument("--test", action="store_true",
                        help="Modalità test: 3 annunci/ricerca, 72h, prime 4+3 ricerche")
    args = parser.parse_args()

    ricerche        = list(RICERCHE)
    ricerche_remote = list(RICERCHE_REMOTE)

    if args.test:
        global MAX_ANNUNCI, HOURS_OLD
        MAX_ANNUNCI     = 3
        HOURS_OLD       = 72
        ricerche        = ricerche[:4]
        ricerche_remote = ricerche_remote[:3]

    # ── Banner ────────────────────────────────────────────────
    print(Fore.CYAN + Style.BRIGHT + """
╔══════════════════════════════════════════════════╗
║        🤖 Job Analyzer Bot v4.0 - Diego          ║
║        Powered by Groq + Llama 3.3 70B           ║
╚══════════════════════════════════════════════════╝
""")
    if args.test:
        print(Fore.YELLOW + Style.BRIGHT +
              f"⚡ MODALITÀ TEST ATTIVA — max {MAX_ANNUNCI} annunci/ricerca, "
              f"ultime {HOURS_OLD}h, "
              f"{len(ricerche)} ricerche Torino + {len(ricerche_remote)} Remote\n")

    # ── Resume ────────────────────────────────────────────────
    risultati_precedenti: list[dict] = []
    # normale: set vuoto → dedup solo interno a ogni loop
    # resume:  set popolato → salta tutto il già visto (condiviso Torino+Remote)
    annunci_gia_visti: set = set()

    if FILE_IN_CORSO.exists() and FILE_IN_CORSO.stat().st_size > 0:
        print(Fore.YELLOW +
              f"⚠  Trovato file in corso: {FILE_IN_CORSO.name} "
              f"({FILE_IN_CORSO.stat().st_size // 1024} KB)")
        risposta = input("   Vuoi riprendere da dove avevi lasciato? [s/N] ").strip().lower()
        if risposta == "s":
            df_prec = pd.read_csv(FILE_IN_CORSO, encoding="utf-8-sig")
            risultati_precedenti = df_prec.to_dict("records")
            annunci_gia_visti = {
                f"{r.get('Titolo', '')}|{r.get('Azienda', '')}"
                for r in risultati_precedenti
            }
            print(Fore.GREEN +
                  f"   ✓ Caricati {len(risultati_precedenti)} annunci già analizzati. "
                  "Verranno saltati.\n")
        else:
            FILE_IN_CORSO.unlink()
            print(Fore.RED + "   File in corso eliminato. Partenza da zero.\n")

    # ── Stato condiviso tra i due loop ────────────────────────
    tot = len(ricerche) + len(ricerche_remote)
    stato: dict = {
        "completate": 0,
        "tot":        tot,
        "inizio":     time.time(),
        "trovati":    0,
        "scartati":   0,
        "analizzati": 0,
    }

    # ── Loop Torino ───────────────────────────────────────────
    risultati_torino = esegui_ricerche(
        ricerche=ricerche,
        location=LOCATION,
        modalita="Torino",
        annunci_gia_visti=annunci_gia_visti,
        stato=stato,
        is_remote=False,
    )

    # ── Loop Full Remote Italia ───────────────────────────────
    print(f"\n{Fore.MAGENTA + Style.BRIGHT}{'─' * 52}")
    print(f"{Fore.MAGENTA + Style.BRIGHT}🌍 FASE 2 — Full Remote Italia ({len(ricerche_remote)} ricerche)")
    print(f"{Fore.MAGENTA + Style.BRIGHT}{'─' * 52}")

    risultati_remote = esegui_ricerche(
        ricerche=ricerche_remote,
        location=LOCATION_REMOTE,
        modalita="Full Remote IT",
        annunci_gia_visti=annunci_gia_visti,
        stato=stato,
        is_remote=True,
    )

    # ── Unione e salvataggio finale ───────────────────────────
    tutti = risultati_precedenti + risultati_torino + risultati_remote

    if not tutti:
        print(f"\n{Fore.RED}Nessun risultato trovato. Prova ad allargare i criteri.")
        if FILE_IN_CORSO.exists():
            FILE_IN_CORSO.unlink()
        return

    df = pd.DataFrame(tutti, columns=COLONNE)
    df["Punteggio AI"] = pd.to_numeric(df["Punteggio AI"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("Punteggio AI", ascending=False).reset_index(drop=True)
    df.to_csv(OUTPUT_FILE, index=False, sep=';', encoding='utf-8-sig')

    if FILE_IN_CORSO.exists():
        FILE_IN_CORSO.unlink()

    # ── Riepilogo finale ──────────────────────────────────────
    candidati_subito = df[df["Consiglio"].str.contains("subito", case=False, na=False)]
    valutabili       = df[df["Consiglio"].str.contains("valutabile", case=False, na=False)]
    n_torino         = len(df[df["Modalità"] == "Torino"])
    n_remote         = len(df[df["Modalità"] == "Full Remote IT"])

    print(Fore.CYAN + Style.BRIGHT + f"""
╔══════════════════════════════════════════════════╗
║                📊 RISULTATI FINALI               ║
╠══════════════════════════════════════════════════╣
║  Annunci trovati:     {str(stato['trovati']):<27}║
║  Scartati (filtro):   {str(stato['scartati']):<27}║
║  Analizzati con AI:   {str(stato['analizzati']):<27}║
║  📍 Torino:           {str(n_torino):<27}║
║  🌍 Full Remote IT:   {str(n_remote):<27}║
║  ✅ Candidati subito: {str(len(candidati_subito)):<27}║
║  🟡 Valutabili:       {str(len(valutabili)):<27}║
║  💾 File salvato:     {OUTPUT_FILE:<27}║
╚══════════════════════════════════════════════════╝
""")

    print(Fore.GREEN + Style.BRIGHT + "🏆 TOP OFFERTE PER DIEGO:\n")
    for i, (_, row) in enumerate(df.head(10).iterrows(), 1):
        print(f"  {i}. {colora_punteggio(row['Punteggio AI'])} | {colora_consiglio(row['Consiglio'])} [{row['Modalità']}]")
        print(f"     📌 {row['Titolo']} @ {row['Azienda']}")
        print(f"     🏠 Smart Working: {row['Smart Working']} | 📝 Contratto: {row['Contratto']}")
        print(f"     💰 RAL stimata: {row['RAL Stimata AI']} | 🏢 Azienda: {row['Reputazione Azienda']}")
        print(f"     💬 {row['Riassunto AI']}")
        print(f"     🔗 {row['Link']}\n")


if __name__ == "__main__":
    main()
