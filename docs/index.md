---
icon: lucide/rocket
---

# dstrack

<figure markdown="span">
  ![dstrack-logo](assets/dstrack-logo.png){ width="200" }
  <figcaption></figcaption>
</figure>

**Dataset versioning and monitoring for the machine learning lifecycle.**

`dstrack` helps data scientists and ML engineers track how datasets evolve over time — catching schema drift, distribution shifts, and unexpected mutations before they silently break pipelines or degrade model performance.

## Features

- **Dataset versioning** — snapshot and compare datasets across pipeline stages
- **Change detection** — identify schema and structural changes between versions
- **Drift monitoring** — detect distribution shifts that can affect model performance
- **Lightweight CLI** — simple command-line interface with no heavy dependencies

## Installation

```bash
pip install dstrack
```

Requires Python 3.11 or later.

## Quickstart

```bash
dstrack
```

## Why dstrack?

Data pipelines break silently. A column gets renamed upstream, a vendor changes a file format, or a feature distribution shifts after a data refresh — and you only find out when model accuracy drops in production.

`dstrack` gives you an audit trail for your datasets so you can catch these problems early, understand what changed, and reproduce any past state of your data.

## License

`dstrack` is distributed under the [MIT License](https://github.com/leoyala/dstrack/blob/main/LICENSE).
