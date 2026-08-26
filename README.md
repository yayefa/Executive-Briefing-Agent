# Executive Threat Advisory Agent (Executive-Briefing-Agent)

An automated security advisory triage and executive threat intelligence agent built with the **Google Agent Development Kit (ADK)**, **Gemini 2.5 Flash**, **Vertex AI Agent Engine (Reasoning Engine)**, and remote **Model Context Protocol (MCP)** servers.

---

## 🏛️ Architecture Overview

The agent automates end-to-end security incident triage and executive advisory generation:

```
[ User Input: Security Bulletin / Advisory URL ]
                       │
                       ▼
               ┌──────────────┐
               │  root_agent  │
               └──────┬───────┘
                      │ delegates to
                      ▼
            ┌──────────────────┐
            │ reporting_agent  │
            └─────────┬────────┘
                      │ invokes
                      ▼
     ┌───────────────────────────────────┐
     │  hunting_workflow (Sequential)    │
     │                                   │
     │ 1. keyword_extractor              │
     │    Scrapes advisory content       │
     │                  │                │
     │ 2. ioc_collector │                │
     │    Queries GTI MCP (/mcp) for     │
     │    threat IOCs (IP/domain/hash)   │
     │                  │                │
     │ 3. investigator  │                │
     │    Queries SecOps MCP (/mcp) for  │
     │    internal matches & telemetry   │
     │                  │                │
     │ 4. consolidator  │                │
     │    Evaluates impact (Yes/No)      │
     └──────────────────┬────────────────┘
                        │
                        ▼
       [ Standardized 🛡️ EXECUTIVE THREAT ADVISORY ]
```

---

## 🚀 Key Features

- **Automated Web Scraping**: Ingests security bulletin URLs and extracts critical vulnerability identifiers, CVEs, and affected vendor technologies.
- **Threat Intelligence Enrichment**: Correlates CVEs with Google Threat Intelligence (GTI) MCP server to obtain threat actors, malware families, and indicators of compromise (IOCs).
- **Internal SecOps Correlation**: Dispatches automated threat hunting queries via Google SecOps (Chronicle) MCP server to detect matching IOCs and affected enterprise assets.
- **Executive Advisory Generation**: Synthesizes findings into a standardized executive report formatted for CISOs and executive leadership.
- **Agent Engine & ADK Ready**: Packaged for local testing with ADK Web UI / CLI and deployment to Vertex AI Agent Engine.

---

## ⚙️ Quick Start

### 1. Prerequisites
- Python >= 3.10
- Google Cloud SDK (`gcloud`) configured
- Access to Vertex AI, Google Threat Intelligence (GTI), and Google SecOps

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/yayefa/Executive-Briefing-Agent.git
cd Executive-Briefing-Agent

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration
Copy `.env.example` to `.env` and fill in your environment variables:
```bash
cp .env.example .env
```

### 4. Local Testing with ADK Web UI
```bash
adk web exec_briefing_agent
```

### 5. Deployment
See [DEPLOYMENT.md](DEPLOYMENT.md) for complete instructions on deploying to Vertex AI Agent Engine and registering with Gemini Enterprise.

---

## 🔗 Acknowledgements & References

This project builds upon and references the foundational work in [twkiiim/exec-briefing-agent](https://github.com/twkiiim/exec-briefing-agent).

