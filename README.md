# 📚 Research, Baby!

hosted on https://researchbaby.streamlit.app 

Interactive web app for quickly exploring a research topic:
<img width="898" alt="image" src="https://github.com/user-attachments/assets/56895622-53c8-42f3-88b7-162573124e35" />

> **Topic → most-cited seed papers → top citing papers**  
> Powered by the [Semantic Scholar Graph API](https://api.semanticscholar.org/graph/v1/).

This app wraps the original `research_baby.py` CLI tool in a simple [Streamlit](https://streamlit.io) GUI so you can share it easily with colleagues.

---

## 🚀 Features

- Search by **topic string** (e.g. `graph neural networks`)
- Filter by **minimum publication year**
- Get the **top-N most-cited seed papers** for that topic
- For each seed, list the **top-K most-cited papers that cite it**
- Works both:
  - as a **web app** (Streamlit Community Cloud)
  - and as the original **CLI script**

---

## 📁 Repository structure

```text
.
├─ app.py             # Streamlit web app (GUI)
├─ research_baby.py   # Original CLI tool + core logic
├─ requirements.txt   # Python dependencies
└─ README.md


