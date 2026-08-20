# enerco-consumption-analysis

HAPAT PER EKZEKUTIMIN E PROJEKTIT

1.Krijimi i mjedisit virtual: 
        python -m venv .venv
        
2. Aktivizimi ne CMD:
        .venv\Scripts\activate
        
3. Instalimi i librarive: 
        python -m pip install -e ".[ui,dev]"
   
4. Kontrolli i projektit:
        python -m enerco_analysis.cli check-setup

5. Vendosja e Excel-it burimor ne folderin: data\input\

6. Ekzekutimi i Pipeline-t:
       python -m enerco_analysis.cli run-all

7. Ekzekutimi i testeve:
       python -m pytest -q
   
8. Nisja e Streamlit per UI:
       python -m streamlit run app.py

   p.s. nese kerkon email kur behet run Streamlit, vetem shtyp ENTER
