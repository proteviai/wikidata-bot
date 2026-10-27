# wikidata-bot

Code of [KnutuxBot](https://www.wikidata.org/wiki/User:KnutuxBot), operated by
[Knutux](https://www.wikidata.org/wiki/User:Knutux).

## Task

Adds "described by source" (P1343) statements citing the 1925 gazetteer
*Lietuvos apgyventos vietos: pirmojo visuotinojo Lietuvos gyventojų 1923 m.
surašymo duomenys* ([Q141805965](https://www.wikidata.org/wiki/Q141805965))
to existing Lithuanian settlement and rural-district (valsčius) items. Each
statement gets the qualifiers P304 (page) and P1810 (name as printed), and a
reference with P854 (the lt.wikisource page) and P813. The book is
transcribed on
[lt.wikisource](https://lt.wikisource.org/wiki/Index:Lietuvos_apgyventos_vietos_1923.pdf).

The bot does not create items or change labels, descriptions or other
statements. It makes one edit per item and skips any item that already
cites the book for that page, so a rerun after a partial failure is safe.
Statements made before 2026-10-27 also repeated the URL as a P2699
qualifier; on a rerun the bot removes that duplicate from them.
Every edit uses `maxlag=5`.

## Usage

```
pip install -r requirements.txt
python -m av.wikidata_write whoami                          # login check, no edits
python -m av.wikidata_write qs FILE                         # dry run
python -m av.wikidata_write qs FILE --upload [--max N] [--pause 9]
```

`FILE` holds QuickStatements-style lines, tab separated, one P1343 statement per line:

```
Q12648347	P1343	Q141805965	P304	"288"	P1810	"Andrešiūnų km."	S854	"https://lt.wikisource.org/wiki/Page:..."	S813	+2026-10-22T00:00:00Z/11
```

The bot reads its credentials from `.env` in the repository root:

```
WIKIDATA_BOT_USERNAME=KnutuxBot@<bot password name>
WIKIDATA_BOT_PASSWORD=...
WIKIDATA_BOT_CONTACT=...        # optional, added to the User-Agent
```

The QuickStatements files are generated in a separate, private working
repository. Settlements are paired with lt.wikipedia's settlement lists, and
each item ID comes from the lt.wikipedia API (`pageprops`). No IDs are typed
by hand. If one item matches several different rows of the book, nothing is
written for it.

## License

MIT
