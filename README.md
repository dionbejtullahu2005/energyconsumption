
## Instalimi dhe ekzekutimi në Windows CMD

1. Hap CMD në folderin kryesor të projektit.
2. Krijo mjedisin virtual:

   python -m venv .venv

3. Aktivizoje:

   .venv\Scripts\activate

4. Instalo varësitë:

   python -m pip install -e ".[ui,dev]"

5. Vendos workbook-un anonim në `data\input\` me emrin e konfiguruar në `config\project.json`.
6. Kontrollo konfigurimin:

   python -m enerco_analysis.cli check-setup

7. Ekzekuto të gjithë pipeline-in në mënyrë të sigurt:

   python -m enerco_analysis.cli run-all

8. Nise UI-në:

   python -m streamlit run app.py

9. Ekzekuto testet:

   python -m pytest -q

`run-all` i krijon rezultatet fillimisht në staging dhe i zëvendëson output-et ekzistuese vetëm nëse të gjashtë hapat përfundojnë me sukses.



## Rrjedha analitike

1. Kontrolli i cilësisë së të dhënave.
2. Transformimi nga formati i gjerë në formatin e gjatë.
3. Metrikat e profilit në nivel kompanie dhe njehsori.
4. Zbulimi i outlier-ëve orarë dhe ndërmjet profileve.
5. Pasurimi me temperaturë, HDD/CDD dhe festa zyrtare.
6. Standardizimi dhe grupimi statistikor i kompanive.

Workbook-u me çelësin konfidencial dhe emrat realë nuk nevojitet për analizën anonime dhe nuk duhet të vendoset në repository. Kodi dhe output-et përdorin vetëm identifikues si `Kompania N`.
