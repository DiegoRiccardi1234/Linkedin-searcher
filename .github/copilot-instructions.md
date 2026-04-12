# 🤖 GitHub Copilot Instructions — Job Analyzer Bot

## Chi sono
Mi chiamo Diego Riccardi, 27 anni, Torino.
Neolaureato in Informatica Triennale all'Università di Torino (95/110, luglio 2025).
Sto cercando il mio primo lavoro vero come dipendente o apprendistato nel settore IT.
Uso intensamente il vibecoding: descrivo cosa voglio in linguaggio naturale e guido
l'AI nella scrittura del codice. Non sono un programmatore classico — sono un
orchestratore di AI con forte capacità logica e analitica.

---

## Il mio profilo tecnico

### Competenze che ho
- Python 3.11 (intermedio — capisco tutto, scrivo con aiuto AI)
- JavaScript / Node.js, React.js, TypeScript (esperienza da tirocinio)
- Java base, C base, SQL, HTML/CSS
- Git, VS Code, Postman
- PostgreSQL, MongoDB
- API REST, autenticazione SSO, RBAC
- Groq API, OpenRouter, LLM in generale

### Come lavoro
- Uso GitHub Copilot e ChatGPT per scrivere e debuggare il codice
- Preferisco capire la logica e l'architettura, non memorizzare la sintassi
- Voglio codice semplice, leggibile, senza over-engineering

### Esperienza reale
- **Tirocinio Finwave S.p.A.** (maggio–settembre 2023, Torino):
  Sviluppo portale B2B cloud in ambito finanziario.
  Stack: React.js, TypeScript, REST API Java Quarkus, PostgreSQL, MongoDB,
  funzionalità multilingua, Single Sign-On, controllo accessi basato su ruoli (RBAC).

---

## Ambiente di sviluppo

- **OS:** Windows 11
- **Python:** 3.11.9 → `C:\Users\diego\AppData\Local\Programs\Python\Python311\python.exe`
- **Editor:** VS Code
- **Cartella progetto:** `D:\DiegoD\Linkedin searcher\`
- **Librerie installate:** `python-jobspy`, `groq`, `pandas`, `colorama`, `pathlib`
- **Comando per avviare:** `python job_analyzer.py` dal PowerShell nella cartella del progetto

---

## File del progetto

### `job_analyzer.py` — Script principale
Pipeline in 4 fasi:
1. **Scraping** da LinkedIn e Indeed via `python-jobspy`
2. **Pre-filtro** con blacklist di frasi (senza AI, veloce)
3. **Analisi AI** di ogni annuncio con Groq API + Llama 3.3 70B
4. **Salvataggio** CSV ordinato per punteggio decrescente

#### Variabili di configurazione (in cima al file)
| Variabile | Valore attuale | Descrizione |
|---|---|---|
| `GROQ_API_KEY` | letta da `groq key.txt` | Chiave API Groq — mai hardcoded nel sorgente |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Modello LLM Groq |
| `RICERCHE` | lista di 50 query | Termini di ricerca lavoro |
| `LOCATION` | `"Torino, Italy"` | Città di ricerca |
| `MAX_ANNUNCI` | `20` | Annunci per ricerca |
| `HOURS_OLD` | `336` | Finestra temporale (14 giorni) |
| `DELAY_TRA_CHIAMATE` | `1.5s` | Pausa tra chiamate Groq API |
| `DELAY_TRA_RICERCHE` | `4.0s` | Pausa tra scraping (evita IP ban) |
| `FILTRO_BLACKLIST` | lista frasi | Frasi che causano scarto senza AI |

#### Logica del pre-filtro
Scarta l'annuncio se `titolo + descrizione` contiene una delle frasi in `FILTRO_BLACKLIST`.
Usa **frasi specifiche** (es. `"senior developer"`) non parole singole (es. `"senior"`)
per evitare falsi positivi su parole come "leading", "principal challenge", ecc.

#### Output JSON dell'analisi AI (per ogni annuncio)
```json
{
  "punteggio": 8,
  "programmazione_richiesta": "Bassa | Media | Alta",
  "smart_working": "Sì | No | Non specificato",
  "contratto": "Dipendente | Apprendistato | Stage | Partita IVA | Non specificato",
  "junior_friendly": "Sì | No",
  "anni_esperienza_richiesti": "0 | 1 | 2 | 3+ | Non specificato",
  "punti_forza_per_diego": "frase",
  "punti_deboli_per_diego": "frase",
  "riassunto": "2 righe",
  "consiglio": "Candidati subito | Valutabile | Salta"
}
Colonne CSV di output (nell'ordine)
Punteggio AI, Consiglio, Titolo, Azienda, Sede, Fonte, Programmazione richiesta, Smart Working, Contratto, Junior Friendly, Anni esperienza richiesti, Punti forza per Diego, Punti deboli per Diego, Riassunto AI, Ricerca usata, Link

Come leggere il CSV
Punteggio 7–10 + "Candidati subito" → apri il link e candidati

Punteggio 5–6 + "Valutabile" → leggi solo se non ci sono opzioni migliori

Punteggio 1–4 + "Salta" → ignora

groq key.txt
Contiene solo la chiave API Groq su una riga sola.
Viene letta così nello script:

python
_key_file    = Path(__file__).parent / "groq key.txt"
GROQ_API_KEY = _key_file.read_text(encoding="utf-8").strip()
Non inserire mai la chiave hardcoded nel codice sorgente.
Non committare mai questo file su Git (aggiungilo a .gitignore).

cv.md — CV di Diego in Markdown
Contiene istruzione, esperienze, competenze e contatti.
Usarlo come riferimento per:

Personalizzare il prompt AI nel job_analyzer.py

Valutare se un ruolo è coerente con il background di Diego

Generare cover letter o messaggi LinkedIn personalizzati

lavori_YYYYMMDD_HHMM.csv — Output generato automaticamente
Un nuovo file viene creato ad ogni esecuzione.
Non modificare i file esistenti — servono come storico dei run precedenti.

Regole per i suggerimenti di Copilot
✅ Cosa voglio
Codice semplice, leggibile, con il minor numero di righe necessarie

Funzioni piccole con una sola responsabilità

Messaggi di errore chiari e in italiano nel terminale

f-string per le stringhe (non concatenazioni con +)

Nomi di variabili in italiano o inglese coerenti col resto del file

Commenti brevi e solo dove la logica non è ovvia

Soluzioni pratiche e immediate, senza classi inutili

❌ Cosa non voglio
Over-engineering (classi, design pattern, astrazioni inutili)

Dipendenze nuove se non strettamente necessarie

Codice che richiede conoscenze avanzate di algoritmi

Soluzioni che rompono la struttura esistente del file

Commenti ridondanti che spiegano l'ovvio

Profilo Diego — per personalizzare analisi e prompt AI
text
Nome: Diego Riccardi
Età: 27 anni
Città: Torino, Piemonte
Laurea: Triennale Informatica UniTo, 95/110, luglio 2025
Master: Cybersecurity (in corso, a.a. 2025/2026, Torino)
Tirocinio: Finwave S.p.A. (maggio–settembre 2023)
  → portale B2B cloud, React.js, TypeScript, Java Quarkus,
    PostgreSQL, MongoDB, SSO, RBAC
Cosa ama: logica, analisi, automazione, AI, gaming
Cosa odia: scrivere codice complesso da zero, Partita IVA
Cerca: dipendente o apprendistato, smart working ibrido o full remote
Ruoli ideali: Analista Funzionale, QA Tester, SOC Analyst,
              AI Automation Specialist, Junior IT Consultant
Punto di forza unico: vibecoding — costruisce strumenti AI complessi
              senza essere un programmatore tradizionale