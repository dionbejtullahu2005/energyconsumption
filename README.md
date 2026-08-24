# Enerco — analiza e profileve të konsumit

Projekt Python për kontrollin e cilësisë, transformimin, profilizimin, zbulimin e outlier-ëve, analizën e motit/festave dhe grupimin statistikor të konsumatorëve anonimë të Enerco.

## Instalimi dhe ekzekutimi në Windows CMD

1. Hap CMD në folderin kryesor të projektit.
2. Krijo mjedisin virtual:

   ```cmd
   python -m venv .venv
   ```

3. Aktivizoje:

   ```cmd
   .venv\Scripts\activate
   ```

4. Instalo varësitë:

   ```cmd
   python -m pip install -e ".[ui,dev]"
   ```

5. Vendos workbook-un anonim në `data\input\` me emrin e konfiguruar në `config\project.json`.
6. Kontrollo konfigurimin:

   ```cmd
   python -m enerco_analysis.cli check-setup
   ```

7. Ekzekuto të gjithë pipeline-in në mënyrë të sigurt:

   ```cmd
   python -m enerco_analysis.cli run-all
   ```

8. Nise UI-në:

   ```cmd
   python -m streamlit run app.py
   ```

9. Ekzekuto testet:

   ```cmd
   python -m pytest -q
   ```

`run-all` i krijon rezultatet fillimisht në staging dhe i zëvendëson output-et ekzistuese vetëm nëse të gjashtë hapat përfundojnë me sukses.

## Rrjedha analitike

1. Kontrolli i cilësisë së të dhënave.
2. Transformimi nga formati i gjerë në formatin e gjatë.
3. Metrikat e profilit në nivel kompanie dhe njehsori.
4. Zbulimi i outlier-ëve orarë dhe ndërmjet profileve.
5. Pasurimi me temperaturë, HDD/CDD dhe festa zyrtare.
6. Standardizimi dhe grupimi statistikor i kompanive.

## Zgjerimi: moti lokal sipas prefiksit të njehsorit

Më 24 gusht 2026, Hapi 5 u zgjerua që temperatura të mos merret më vetëm nga Prishtina. Prefiksi në fillim të ID-së së njehsorit lidhet me distriktin:

| Prefiksi | Distrikti |
|---|---|
| DPR | Prishtinë |
| DPZ | Prizren |
| DPE | Pejë |
| DMI | Mitrovicë |
| DGL | Gjilan |
| DFE | Ferizaj |
| DGJ | Gjakovë |

Koordinatat dhe kjo hartë ruhen në `config/project.json`. Temperaturat historike merren nga Open-Meteo Historical Weather API për periudhën e analizës dhe ruhen lokalisht, prandaj analiza e zakonshme mund të përsëritet pa kërkesa të reja interneti.

Për rifreskimin e burimit të motit:

```cmd
python scripts\download_district_weather.py
```

Pastaj mund të ekzekutohet vetëm Hapi 5:

```cmd
python -m enerco_analysis.cli external-factors
```

ose i gjithë procesi:

```cmd
python -m enerco_analysis.cli run-all
```

### Si llogaritet temperatura e kompanisë

- Kompani me njehsorë në një distrikt: përdoret temperatura lokale e atij distrikti.
- Kompani me njehsorë në disa distrikte: përdoret mesatarja e temperaturave lokale me pesha fikse sipas energjisë historike të njehsorëve të kompanisë në secilin distrikt.

Për kompaninë `c`, distriktin `d` dhe orën/ditën `t`:

```text
pesha(c,d) = energjia_historike(c,d) / energjia_historike_totale(c)
temperatura(c,t) = Σ [pesha(c,d) × temperatura(d,t)]
```

Pesha është historike dhe fikse. Nuk përdoret konsumi i ditës që po analizohet, sepse kjo do të mund të krijonte artificialisht lidhje temperaturë–konsum.

HDD dhe CDD agregohen me të njëjtat pesha. Pragu bazë aktual është 18 °C, sipas konfigurimit.

### Ndryshimet teknike të realizuara

- `config/project.json`: u shtuan shtatë prefikset, emrat e distrikteve, koordinatat dhe emri i burimit të ri.
- `scripts/download_district_weather.py`: shkarkon dhe ruan në mënyrë atomike motin historik për të shtatë qytetet.
- `src/enerco_analysis/external_factors.py`: nxjerr prefiksin, validon mbulimin, lidh motin lokal, ndërton peshat historike dhe pasuron analizën/outlier-ët.
- `src/enerco_analysis/cli.py`: komanda `external-factors` përdor burimin e konfiguruar dhe hartën e distrikteve.
- `src/enerco_analysis/pipeline.py`: `run-all` kopjon burimin e ri në staging dhe ruan sjelljen e sigurt të promovimit.
- `app.py`: u shtua filtri sipas distriktit, distrikti i kompanisë/njehsorit dhe përshkrimi i metodës së temperaturës.
- `tests/test_external_factors.py` dhe `tests/test_pipeline.py`: u shtuan/verifikuan nxjerrja e prefiksit, ponderimi i kompanive multidistrikt dhe integrimi me pipeline-in.

### Output-et e reja ose të ndryshuara

- `data/external/kosovo_district_weather_raw.json` — përgjigjet burimore për shtatë qytetet.
- `data/external/kosovo_district_weather_hourly.parquet` — moti orar sipas prefiksit/distriktit.
- `data/external/kosovo_district_weather_daily.parquet` — moti ditor dhe HDD/CDD sipas distriktit.
- `data/processed/hourly_consumption_enriched.parquet` — çdo lexim me `meter_prefix`, `district` dhe motin lokal.
- `data/processed/company_district_membership.parquet` — distriktet, energjia historike dhe pesha e secilës kompani.
- `data/processed/company_daily_energy_enriched.parquet` — konsum dhe mot lokal/ponderuar në nivel kompanie.
- `data/processed/meter_weather_sensitivity.parquet` — ndjeshmëria e njehsorit me temperaturën e distriktit të vet.
- `data/processed/company_weather_sensitivity.parquet` — ndjeshmëria e kompanisë dhe metoda territoriale.
- `outputs/weather_data_quality.xlsx` — përfshin fletën `District sources`.
- `outputs/weather_holiday_analysis.xlsx` — përfshin fletën `District membership`.
- `outputs/outlier_report_enriched.xlsx` — outlier-ët me motin lokal dhe metodën territoriale.

Skedarët e vjetër me emrin `prishtina_weather_*` nuk përdoren më nga kodi i ri dhe mund të hiqen nga dorëzimi final pasi të jetë konfirmuar pipeline-i i ri.

### Validimi i realizuar

- U gjetën 499 njehsorë dhe vetëm shtatë prefikset e konfiguruara; 0 prefikse të panjohura.
- U përpunuan 4,901,160 rreshta orarë; 0 temperatura mungojnë pas lidhjes.
- Moti përmban 66,360 rreshta orarë: 9,480 orë × 7 distrikte.
- U krijuan 499 profile moti të njehsorëve dhe 81 profile moti të kompanive.
- 29 kompani kanë njehsorë në më shumë se një distrikt dhe përdorin ponderimin historik.
- Të 16 testet automatike kalojnë.
- UI u testua me një instancë të pastër Streamlit: filtri territorial dhe paneli i motit lokal shfaqen pa gabime.

## Shënim për OneDrive

Nëse workbook-u shfaqet me atributin `ReparsePoint` dhe `run-all` jep `PermissionError`, sigurohu që skedari të jetë mbyllur në Excel dhe në Windows zgjidh **Always keep on this device**. Pastaj ekzekuto përsëri `run-all`. Një dështim i tillë nuk i mbishkruan rezultatet ekzistuese.

## Zgjerimi: energjia totale sipas grupit të klasterizimit

Më 24 gusht 2026, tabela përmbledhëse e grupeve u zgjerua me energjinë totale të kompanive brenda secilit grup. Për çdo grup llogariten:

- `company_count` — numri unik i kompanive në grup;
- `total_energy_kwh` — shuma e energjisë reale të kompanive në grup;
- `total_energy_mwh` — e njëjta shumë e konvertuar në MWh për paraqitje në UI.

Formula është:

```text
Energjia totale e grupit = Σ energy_total_kwh e kompanive të caktuara në atë grup
MWh = kWh / 1000
```

Vlera llogaritet në `src/enerco_analysis/clustering.py`, ruhet në `data/processed/cluster_centers.parquet`, shkruhet në fletën `Cluster centers` të `outputs/company_clustering.xlsx` dhe shfaqet në tabin `Grupet` të UI-së.

Pas rifreskimit aktual:

| Grupi | Numri i kompanive | Energjia totale |
|---|---:|---:|
| Grupi 1 | 52 | 62,112.75 MWh |
| Grupi 2 | 27 | 4,854.81 MWh |
| Gjithsej të klasterizuara | 79 | 66,967.56 MWh |

Dy nga 81 kompanitë nuk kanë të gjitha metrikat e nevojshme për klasterizim. Ato mbeten në `cluster_excluded_companies.parquet` dhe energjia e tyre nuk përfshihet në totalet e grupeve, sepse nuk i përkasin asnjë grupi.

U shtua edhe një test automatik që verifikon numrin e kompanive, shumën në kWh dhe konvertimin në MWh për secilin grup.

UI përmban edhe kompatibilitet me output-et e versionit të mëparshëm: nëse
`cluster_centers.parquet` nuk e ka ende kolonën `total_energy_mwh`, ajo llogaritet
automatikisht nga `company_clusters.parquet`. Kjo shmang gabimin
`KeyError: total_energy_mwh`; ekzekutimi i `cluster-companies` vazhdon të jetë
mënyra e rekomanduar për t'i ruajtur kolonat e reja edhe fizikisht në output.

## Zgjerimi: zgjedhja Prishtinë proxy / temperaturë sipas distriktit

Më 24 gusht 2026, në sidebar të UI-së u shtua toggle-i `Temperatura sipas distriktit`:

- `OFF` — temperatura e Prishtinës përdoret si proxy për të gjithë njehsorët;
- `ON` — temperatura lidhet me prefiksin e njehsorit dhe distriktin përkatës;
- për kompanitë me disa distrikte, mënyra `ON` përdor mesataren lokale të ponderuar me energjinë historike.

Toggle-i ndryshon vetëm rezultatet e varura nga moti: korelacionin temperaturë–konsum,
HDD–konsum, CDD–konsum, grafikun temperaturë–konsum dhe temperaturën/kontekstin termik
të outlier-ëve. Energjia totale, raporti pik/jo-pik, raporti javë/fundjavë, CV dhe load
factor nuk ndryshojnë, sepse llogariten vetëm nga leximet e njehsorëve.

Hapi 5 krijon dhe ruan paralelisht të dy skenarët:

- `company_daily_energy_enriched.parquet` — moti sipas distriktit;
- `company_weather_sensitivity.parquet` — ndjeshmëria sipas distriktit;
- `company_hourly_outliers_enriched.parquet` — outlier-ët sipas distriktit;
- `company_daily_energy_prishtina_proxy.parquet` — moti me Prishtinën proxy;
- `company_weather_sensitivity_prishtina_proxy.parquet` — ndjeshmëria me proxy;
- `company_hourly_outliers_prishtina_proxy.parquet` — outlier-ët me proxy.

Në workbook-un `weather_holiday_analysis.xlsx`, skenari proxy ruhet në fletën
`Company weather proxy`. Në `outlier_report_enriched.xlsx` ruhet në fletën
`Outliers Prishtina proxy`.

Validimi për `Kompania 1` tregoi se toggle-i ndryshon metrikat e motit:

| Metrika | Prishtina proxy (OFF) | Sipas distriktit (ON) |
|---|---:|---:|
| Temperaturë–konsum | -0.7106 | -0.7259 |
| HDD–konsum | 0.7377 | 0.7548 |
| CDD–konsum | -0.4461 | -0.5010 |

Shuma e energjisë u verifikua identike në të dy skenarët.

## Zgjerimi: filtri i datave zbatohet në rezultatet e UI-së

Më 24 gusht 2026, filtri `Periudha` u lidh me llogaritjet e UI-së. Pas zgjedhjes
së datës fillestare dhe përfundimtare, nga intervali i zgjedhur rillogariten:

- energjia totale e kompanisë;
- raporti pik/jo-pik;
- raporti javë/fundjavë;
- load factor dhe mbulimi;
- profili mesatar 24-orësh dhe konsumi mujor;
- korelacionet temperaturë–konsum, HDD–konsum dhe CDD–konsum;
- efekti mesatar i festave;
- grafiku temperaturë–konsum dhe lista e festave;
- numri dhe paraqitja e outlier-ëve;
- energjia, raportet, CV dhe load factor i njehsorit të zgjedhur;
- konsumi/injektimi mujor i prosumer-it;
- energjia totale e kompanive brenda secilit grup të klasterizimit.

Periudha është përfshirëse: të dhënat e datës së fillimit dhe të datës së
përfundimit hyjnë në rezultat. UI shfaq edhe tekstin `Periudha aktive e
rezultateve` për ta bërë të dukshëm intervalin që po përdoret.

Dy rezultate mbeten referenca të pipeline-it bazë dhe nuk rikrijohen nga filtri:

- `Grupi ID` dhe përshkrimi i grupit, sepse riklasterizimi për çdo ndryshim date
  do ta ndryshonte vetë kuptimin e grupeve; megjithatë energjia në tabelën e
  grupeve rillogaritet për periudhën e zgjedhur;
- statusi i cilësisë, sepse është raport diagnostik i prodhuar nga Hapi 1.

Kur periudha e zgjedhur ka më pak se 90 ditë të vlefshme, UI paraqet paralajmërim
se korelacionet me motin duhet të interpretohen me kujdes.

## Zgjerimi: eksportimi i listave të kompanive sipas grupit

Në tabin `Grupet` u shtua butoni `Eksporto listat e grupeve në Excel`. Butoni
krijon një workbook `.xlsx` me:

- fletën `Përmbledhje`, me përshkrimin, numrin e kompanive dhe energjinë totale
  në kWh/MWh për secilin grup;
- një fletë të veçantë `Grupi 1`, `Grupi 2`, etj., me kompanitë anonime që i
  përkasin grupit;
- energjinë e secilës kompani vetëm për periudhën aktuale të filtrit të datave;
- datën fillestare dhe përfundimtare të përdorur në eksport.

Emri i skedarit përmban periudhën, p.sh.:

```text
kompanite_sipas_grupeve_2025-06-30_2025-07-31.xlsx
```

Workbook-u nuk përmban emra realë ose çelësin konfidencial. Përmbledhja përdor
formula Excel për numrin e kompanive dhe totalet e energjisë, ndërsa fletët kanë
filtra, tituj të formatuar, data dhe vlera numerike të përdorshme për analizë.

Eksporti validohet teknikisht duke u rihapur pas krijimit dhe mbulohet me test
automatik për fletët, kompanitë dhe formulat e totalit.

## Privatësia

Workbook-u me çelësin konfidencial dhe emrat realë nuk nevojitet për analizën anonime dhe nuk duhet të vendoset në repository. Kodi dhe output-et përdorin vetëm identifikues si `Kompania N`.
