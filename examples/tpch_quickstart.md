# TPC-H Quickstart

## Setup

```bash
pip install pynemo
nemo init myproject
cd myproject
```

## Load demo data

```bash
nemo add --tpch --scale 1
nemo ls
nemo profile orders
```

## Run exploration

```bash
nemo run --minutes 10
```

## Review findings

```bash
nemo brief --output reports/morning_brief.md
nemo graph stats
nemo graph contradictions --top 5
```
