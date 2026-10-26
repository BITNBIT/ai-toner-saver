![copertina](.github/copertina.png)

# AI Toner Saver 1.7.1

Pulisce PDF e foto di documenti per stampare in bianco e nero consumando meno toner:
toglie il fondo grigio delle foto, scontorna il foglio, raddrizza e impagina in A4.
Funziona tutto offline, sul computer. Per Windows 10/11, con qualsiasi stampante.

☕ Offrimi un caffè: https://buymeacoffee.com/bitnbit · YouTube: https://www.youtube.com/@bitnbit · Sito: https://bitnbit.com

*Cleans up PDFs and phone photos of documents for black-and-white printing with less toner:
automatic page detection and perspective crop, background removal, deskew, A4 layout
(ID cards at real size). Offline, Windows, Python + PySide6. Interface in Italian.*

## Download e avvio
Dalla pagina **[Releases](https://github.com/BITNBIT/ai-toner-saver/releases/latest)** scegli:

**Versione portatile (consigliata)** — `AI_Toner_Saver_Portatile_v1.7.1.zip`
1. Estrai lo zip dove vuoi (anche su una chiavetta USB).
2. Doppio clic su **AVVIA.bat**. Fatto: Python e librerie sono già dentro, non serve installare nulla
   né internet.

Vive tutto nella sua cartella: impostazioni (`impostazioni.ini`) ed eventuale registro errori
restano lì. Per eliminarla basta cancellare la cartella.

**Versione leggera** — `AI_Toner_Saver_v1.7.1.zip` (o pulsante verde **Code ▸ Download ZIP**)
1. Installa [Python 3.11](https://www.python.org/downloads/) (spunta «Add python.exe to PATH»).
2. Estrai lo zip e fai doppio clic su **AVVIA.bat**. La prima volta scarica le librerie
   (qualche minuto, serve internet), poi si apre subito.

## Novità rispetto alla 1.6
- **Tanti file insieme**: selezione multipla, intere cartelle, trascinamento nella finestra,
  PDF multipagina e TIFF multipagina. Elenco pagine con miniature: trascina per riordinare,
  togli la spunta per non stampare una pagina.
- **Scontorno automatico nuovo**: prova cinque strategie diverse (luminosità, colore del piano,
  carta bianca, bordi netti, bordi deboli) e sceglie quella con il bordo più continuo.
  Funziona anche con ombre forti, foglio bianco su tavolo chiaro, tessere su legno, scontrini.
  Se non è sicuro **non taglia nulla** e mostra l'avviso arancione «Controlla ora».
  Le scansioni non vengono mai ritagliate da sole.
- **Editor dei bordi** con 4 punti già posizionati da trascinare e lente d'ingrandimento
  (doppio clic su una pagina, oppure «Bordi…»). Vale anche per i PDF.
- **Impaginazione automatica**: i documenti riempiono l'A4 (verticale o orizzontale secondo il
  contenuto); le **tessere** (carta d'identità, patente, tessera sanitaria) vengono riconosciute e
  stampate **a grandezza reale**, fronte e retro sullo stesso foglio. Altre scelte: adatta all'A4,
  tessera a grandezza reale, larghezza personalizzata, 2 o 4 pagine per foglio.
- **Modalità automatica intelligente**: riconosce se la pagina è testo, testo e immagini o foto e
  regola la pulizia. Le foto del viso e le fasce colorate non vengono più cancellate.
- **Schiarisci zone piene**: alleggerisce l'interno di fasce nere, foto e riquadri pieni lasciando
  intatti testo e contorni (grande risparmio sui moduli con intestazioni nere).
- **Pagine vuote** riconosciute ed escluse da sole.
- **Ruota** ⟲ ⟳ e modalità diversa per singola pagina (tasto destro sulla pagina).
- **Salvataggio**: un unico PDF oppure un PDF per ogni file; i PDF in bianco e nero puro sono più leggeri.
- **Cartella automatica**: scegli una cartella; ogni PDF o foto copiato lì dentro viene pulito e
  salvato da solo nella sottocartella «Puliti».
- **Più veloce**: le pagine vengono elaborate in parallelo; ogni modifica alle impostazioni
  aggiorna subito l'anteprima, senza premere pulsanti.
- **Una sola finestra**: i file aperti da Esplora risorse, anche molti insieme, arrivano tutti
  nella finestra già aperta.
- Avvio senza finestra nera; le librerie si installano solo la prima volta.

## Integrazione con Windows (facoltativa)
**CONTEXT_MENU_INSTALLA.bat** aggiunge
   - tasto destro su file o cartelle ▸ Mostra altre opzioni ▸ «Ottimizza con AI Toner Saver»
   - tasto destro ▸ Invia a ▸ «AI Toner Saver» (comodo con molti file)
   - collegamento sul Desktop.
   **CONTEXT_MENU_RIMUOVI.bat** toglie tutto.

## Scorciatoie
Ctrl+O aggiungi file · Ctrl+S salva PDF · Ctrl+P stampa · Ctrl+B bordi ·
Ctrl+L / Ctrl+R ruota · Canc togli pagina.

## Da sapere
- Controlla sempre l'anteprima: numeri, firme, timbri e testi piccoli.
- La copertura indicata è una stima sull'immagine, non una misura del toner consumato.
- I PDF esportati sono immagini (non testo selezionabile).
- Non è una stampante virtuale. Non usa la scheda video né modelli di intelligenza artificiale
  scaricati: lo scontorno e la pulizia sono algoritmi di visione artificiale.
- Non alterare dati e dettagli ufficiali dei documenti d'identità; un riflesso sulla foto
  non può essere ricostruito.
- In caso di errore i dettagli sono in `errori.log` nella cartella (versione portatile) oppure in
  `%LOCALAPPDATA%\AITonerSaver\errori.log` (versione leggera).
- L'integrazione con Windows (menu del tasto destro, «Invia a», collegamento sul Desktop) è l'unica
  cosa che scrive fuori dalla cartella, e solo se lanci CONTEXT_MENU_INSTALLA.bat.
- Nella stampa scegli A4 e scala 100% nel driver della stampante.
