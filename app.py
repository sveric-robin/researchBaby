import os
from typing import Dict, List

import streamlit as st

from research_baby import (
    search_top_papers,
    get_top_citing_papers,
    format_paper_line,
    Paper,
)

st.set_page_config(
    page_title="Research, Baby!",
    page_icon="📚",
    layout="wide",
)

st.title("📚 Research, Baby!")
st.caption("Topic → most-cited seeds → top citing papers (Semantic Scholar Graph API)")

with st.sidebar:
    st.header("Settings")

    query = st.text_input(
        "Topic query",
        value="Ultrasound is life",
        help="Same as the --query CLI flag.",
    )

    min_year = st.number_input(
        "Minimum publication year",
        min_value=1900,
        max_value=2100,
        value=2021,
        step=1,
        help="Same as --min-year.",
    )

    seeds = st.slider(
        "Number of seed papers",
        min_value=1,
        max_value=30,
        value=10,
        help="Same as --seeds.",
    )

    children_count = st.slider(
        "Citing papers per seed",
        min_value=1,
        max_value=20,
        value=5,
        help="Same as --children.",
    )

    st.markdown("---")
    api_key_input = st.text_input(
        "Semantic Scholar API key (optional)",
        type="password",
        help="If you have one, paste it here. Otherwise the app uses anonymous access.",
    )
    if api_key_input:
        # Will be picked up by research_baby._make_session()
        os.environ["S2_API_KEY"] = api_key_input

    st.markdown(
        """
        ℹ️ The app uses politeness & retry logic so it may take a few seconds for large searches.
        A useful thing by: robin.kerstens@uantwerpen.be 
        """
    )

# Simple state container so results persist after button click
if "results" not in st.session_state:
    st.session_state.results = None


run_clicked = st.button("🔍 Run search", type="primary", use_container_width=True)

if run_clicked:
    if not query.strip():
        st.error("Please enter a topic query.")
    else:
        with st.spinner("Fetching papers from Semantic Scholar…"):
            seeds_list: List[Paper] = search_top_papers(query, int(min_year), int(seeds))

            if not seeds_list:
                st.warning(
                    f"No papers found for '{query}' with year ≥ {min_year}. "
                    "Try lowering the year or broadening the query."
                )
                st.session_state.results = None
            else:
                children_map: Dict[str, List[Paper]] = {}
                for seed in seeds_list:
                    try:
                        kids = get_top_citing_papers(seed.paper_id, int(children_count))
                    except Exception:
                        kids = []
                    children_map[seed.paper_id] = kids

                st.session_state.results = {
                    "query": query,
                    "min_year": int(min_year),
                    "seeds": seeds_list,
                    "children_map": children_map,
                    "children_count": int(children_count),
                }

results = st.session_state.results

if results:
    st.markdown("---")
    st.subheader("Results")

    st.write(
        f"**Topic:** `{results['query']}`  "
        f"• **Year cutoff:** ≥ {results['min_year']}  "
        f"• **Seeds:** {len(results['seeds'])}  "
        f"• **Children per seed:** {results['children_count']}"
    )

    for idx, seed in enumerate(results["seeds"], start=1):
        # Build a nicer header line than the CLI
        year = seed.year or "n/a"
        cites = seed.citation_count or 0
        doi = (seed.external_ids or {}).get("DOI")
        link = f"https://doi.org/{doi}" if doi else (seed.url or "")

        header = f"{idx}. {seed.title} ({year}) — {cites} cites"
        if link:
            header += " 🔗"

        with st.expander(header, expanded=(idx == 1)):
            if link:
                st.markdown(f"[Open paper]({link})")

            st.markdown("**Top citing papers:**")

            kids: List[Paper] = results["children_map"].get(seed.paper_id, [])

            if not kids:
                st.write("_No citing papers found (or fetch failed for this seed)._")
            else:
                for j, child in enumerate(kids, start=1):
                    cyear = child.year or "n/a"
                    ccites = child.citation_count or 0
                    cdoi = (child.external_ids or {}).get("DOI")
                    clink = f"https://doi.org/{cdoi}" if cdoi else (child.url or "")

                    line = f"{j}. {child.title} ({cyear}) — {ccites} cites"
                    if clink:
                        st.markdown(f"{line}  🔗 [{clink}]({clink})")
                    else:
                        st.markdown(line)

    st.markdown("---")
    st.caption("Built on Semantic Scholar Graph API · backed by your original research_baby.py")
