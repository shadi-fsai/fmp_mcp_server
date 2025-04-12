# Financial Modeling Prep (FMP) MCP Server

A Model Context Protocol (MCP) server that provides access to Financial Modeling Prep (FMP) API data through a standardized interface. This server allows AI assistants like Claude to access financial data programmatically.

## Features

- **Company Profiles**: Access company information, descriptions, market caps, employee counts, and industry data
- **Financial Statements**: Retrieve income statements, balance sheets, and cash flow statements
- **Financial Metrics**: Get key metrics, ratios, and growth data
- **Analyst Data**: Access analyst estimates and recommendations
- **SEC Filings**: Find and retrieve SEC filing content
- **Earnings Transcripts**: Get earnings call transcripts
- **Market Data**: Access current stock prices and treasury yields
- **Competitor Analysis**: Find competitor companies

## Installation

### Prerequisites

- Python 3.8 or higher
- UV package manager (recommended) or pip
- Financial Modeling Prep API key

### Setup

1. Clone this repository

2. Create a `.env` file in the project root with your API key:
   ```
   # Financial Modeling Prep API Configuration
   FMP_KEY=your_api_key_here
   
   # Optional: SEC API Configuration
   SEC_ACCESS=YourCompanyName YourEmail@example.com
   ```

3. Install dependencies using UV (recommended):
   ```bash
   uv venv
   uv pip install -r requirements.txt
   ```

   Or using pip:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Server

### Using UV (Recommended)

UV provides faster dependency resolution and installation. To run the server with UV:

```bash
# Activate the virtual environment
uv venv activate

# Run the server
python fmp_mcp_server.py
```

The server will start and listen for connections on the default MCP port.

### Using pip

```bash
# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Run the server
python fmp_mcp_server.py
```

## Connecting with Claude Desktop

Claude Desktop can connect to MCP servers to access financial data. Here's how to set it up:

1. Download Claude Desktop
2. Edit claude_desktop_config.json:
    	"fmp_mcp_server": {
            "command": "uv",
            "args": [
                "--directory",
                "REPLACE ME WITH ABSOLUTE DIRECTORY TO REPO",
                "run",
                "fmp_mcp_server.py"
	    ]
         }
 


Now Claude can use the FMP data through the MCP interface. You can ask Claude to:
- Get company profiles
- Retrieve financial statements
- Find SEC filings
- Access market data
- And more!

## Example Queries for Claude

Once connected, you can ask Claude questions like:

- "I am considering a 3 year horizon investment, is Apple a good investment?"
- "Show me Tesla's latest quarterly income statement"
- "Find the latest 10-K filing for Microsoft"
- "What are Amazon's main competitors?"
- "Get the latest earnings transcript for Meta"

## Configuration Options

The server supports the following environment variables:

- `FMP_KEY`: Your Financial Modeling Prep API key (required)
- `SEC_ACCESS`: Your company name and email for SEC API access (optional)

## Caching

The server implements a caching system to reduce API calls and improve performance:
- Financial data is cached by quarter/year
- Profile data is cached monthly
- Daily price data is cached for the current day

Cache files are stored in the `DataCache` directory.

## Logging

Logs are written to the `logs` directory with rotation enabled:
- Maximum log file size: 10MB
- Number of backup files: 5

## License

[MIT License](LICENSE)

## Acknowledgements

- [Financial Modeling Prep](https://financialmodelingprep.com/) for providing the API
- [MCP Server](https://github.com/anthropics/mcp-server) for the Model Context Protocol implementation
