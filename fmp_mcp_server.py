#!/usr/bin/env python3
"""
Financial Modeling Prep (FMP) MCP Server
A server that connects to the FMP API to provide financial data via the Model Context Protocol (MCP)

Version: 1.0.0
"""

import os
import sys
import logging
import datetime
import json
import requests
import csv
import re
import html2text
import certifi
import dotenv
from typing import List, Dict, Any, Optional, Union

# Load environment variables from .env file
dotenv.load_dotenv()

# Import MCP
# Ensure 'mcp-server' is installed: pip install mcp-server
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: mcp-server library not found. Please install it using 'pip install mcp-server'", file=sys.stderr)
    sys.exit(1)

# --- Configuration ---

# Load API Key from environment variable
FMP_API_KEY = os.environ.get('FMP_KEY')

if not FMP_API_KEY:
    print("Error: FMP_KEY environment variable not set. Please set it to your Financial Modeling Prep API key.", file=sys.stderr)
    sys.exit(1)

# Cache directory configuration
# Use a directory named 'DataCache' in the same directory as the script
current_dir = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(current_dir, 'DataCache')
if not os.path.exists(CACHE_DIR):
    try:
        os.makedirs(CACHE_DIR)
    except OSError as e:
        print(f"Error creating cache directory {CACHE_DIR}: {e}", file=sys.stderr)
        # Attempt to proceed without caching if directory creation fails
        CACHE_DIR = None

# Configure logging
log_file = os.path.join(current_dir, 'fmp_mcp_server.log')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler(log_file)]
)
logger = logging.getLogger("fmp-mcp-server")

# Log server version
VERSION = "1.0.0"
logger.info(f"Starting Financial Modeling Prep MCP Server v{VERSION}")
if not CACHE_DIR:
    logger.warning("Cache directory could not be created. Caching will be disabled.")
else:
    logger.info(f"Using cache directory: {CACHE_DIR}")


# --- Global Caches ---
# These will be populated lazily when needed
TodayPrices: Dict[str, float] = {}
savedProfile: Dict[str, Dict[str, Any]] = {}


# --- Helper Functions (Internal) ---

def get_jsonparsed_data(url: str) -> Optional[Union[List[Any], Dict[str, Any]]]:
    """Fetches and parses JSON data from a URL."""
    try:
        response = requests.get(url, verify=certifi.where(), timeout=15) # Added timeout
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        # Check for empty or invalid JSON response
        if not response.text:
            logger.warning(f"Empty response received from {url}")
            return None
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP request failed for {url}: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from {url}: {e}")
        logger.debug(f"Response text: {response.text[:500]}...") # Log part of the response
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred in get_jsonparsed_data for {url}: {e}")
        return None

def get_cached_fetch(url: str, filename_prefix: str) -> Optional[Union[List[Any], Dict[str, Any]]]:
    """Fetches data from URL, using a local cache file based on quarter/year."""
    if not CACHE_DIR: # Don't cache if directory doesn't exist
        return get_jsonparsed_data(url)

    # Create a timestamp suffix based on current quarter and year
    now = datetime.datetime.now()
    quarter = (now.month - 1) // 3 + 1
    year = now.year
    timestamp_suffix = f"{year}_Q{quarter}"
    filename = os.path.join(CACHE_DIR, f"{filename_prefix}_{timestamp_suffix}.json")

    # Check if the local cache file exists
    if os.path.exists(filename):
        logger.debug(f"Cache hit for {filename_prefix}. Loading from {filename}")
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            logger.error(f"Error reading cache file {filename}: {e}. Fetching fresh data.")
            # If cache is corrupt, try fetching fresh data

    # Fetch data from the URL
    logger.debug(f"Cache miss for {filename_prefix}. Fetching from {url}")
    j = get_jsonparsed_data(url)

    # Save the fetched data to the local cache file if successful
    if j is not None:
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(j, f, ensure_ascii=False, indent=4) # Added indent for readability
            logger.debug(f"Saved fetched data to cache file: {filename}")
        except (OSError, TypeError) as e:
            logger.error(f"Error writing cache file {filename}: {e}")
            # Proceed with the fetched data even if caching fails

    return j

def load_csv_to_json(filename: str) -> Optional[List[Dict[str, str]]]:
    """Loads CSV data into a list of dictionaries."""
    data = []
    try:
        # Specify the encoding as utf-8-sig to handle potential BOM
        with open(filename, 'r', encoding='utf-8-sig') as file:
            reader = csv.DictReader(file)
            for row in reader:
                data.append(row)
        return data
    except FileNotFoundError:
        logger.error(f"CSV file not found: {filename}")
        return None
    except Exception as e:
        logger.error(f"Error reading CSV file {filename}: {e}")
        return None

def initialize_saved_profile_cache() -> bool:
    """Initializes the global savedProfile cache from a potentially cached CSV file."""
    global savedProfile
    if len(savedProfile) > 0:
        logger.debug("Profile cache already initialized.")
        return True

    if not CACHE_DIR:
        logger.warning("Cannot initialize profile cache because cache directory is unavailable.")
        return False

    now = datetime.datetime.now()
    year = now.year
    month = now.month
    csv_filename = os.path.join(CACHE_DIR, f"profile_bulk_{year}_{month}.csv")
    csv_url = f"https://financialmodelingprep.com/api/v4/profile/all?apikey={FMP_API_KEY}"

    if not os.path.exists(csv_filename):
        logger.info(f"Profile cache CSV {csv_filename} not found. Downloading from API.")
        try:
            response = requests.get(csv_url, timeout=60) # Longer timeout for bulk download
            response.raise_for_status()
            with open(csv_filename, 'wb') as file:
                file.write(response.content)
            logger.info(f"Successfully downloaded profile cache CSV to {csv_filename}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch the profile CSV file from the API: {e}")
            return False
        except OSError as e:
            logger.error(f"Failed to write profile CSV file {csv_filename}: {e}")
            return False

    json_data = load_csv_to_json(csv_filename)
    if json_data is None:
        logger.error("Failed to load profile data from CSV.")
        # Attempt to delete potentially corrupt CSV?
        # try: os.remove(csv_filename) except OSError: pass
        return False

    # Clear previous cache content before repopulating
    savedProfile.clear()
    for row in json_data:
        # Ensure 'Symbol' key exists and is not empty
        symbol_key = "Symbol" # FMP API uses "Symbol"
        if symbol_key in row and row[symbol_key]:
            savedProfile[row[symbol_key]] = row
        else:
             logger.warning(f"Skipping row due to missing or empty symbol: {row}")

    logger.info(f"Profile cache initialized with {len(savedProfile)} entries.")
    return True


# --- MCP Server Setup ---
mcp = FastMCP("FinancialModelingPrepMCP")


# --- MCP Tools & Resources ---

@mcp.tool()
def get_todays_price(ticker: str) -> Dict[str, Any]:
    """
    Get the latest available price for a given stock ticker from NASDAQ, NYSE, or AMEX.
    Prices are cached for the current day across exchanges.

    Args:
        ticker: The stock symbol (e.g., AAPL, MSFT).
    """
    logger.info(f"Tool request: get_todays_price for {ticker}")
    global TodayPrices
    ticker = ticker.upper() # Standardize ticker

    if ticker in TodayPrices:
        logger.debug(f"Price cache hit for {ticker}")
        return {"symbol": ticker, "price": TodayPrices[ticker]}
    else:
        logger.info(f"Price cache miss for {ticker}. Fetching daily quotes.")
        today = datetime.date.today().strftime("%Y-%m-%d")
        exchanges = ["NASDAQ", "NYSE", "AMEX"]
        new_prices_found = 0

        # Clear old cache before fetching new data
        TodayPrices.clear()

        for exchange in exchanges:
            url = f"https://financialmodelingprep.com/api/v3/quotes/{exchange}?apikey={FMP_API_KEY}"
            fileName_prefix = f"{exchange}_quotes_{today}" # Make prefix more specific
            # Use get_cached_fetch which includes quarter/year logic if needed for other data,
            # but for daily prices, a daily cache might be more appropriate.
            # For simplicity, using the existing get_cached_fetch.
            # Consider a dedicated daily cache helper if performance is critical.
            j = get_cached_fetch(url, fileName_prefix)

            if isinstance(j, list):
                logger.debug(f"Processing {len(j)} quotes from {exchange}")
                for item in j:
                    if isinstance(item, dict) and 'symbol' in item and 'price' in item:
                         # Only update if price is valid (not None or 0?)
                         if item['price'] is not None:
                             TodayPrices[item['symbol']] = item['price']
                             new_prices_found += 1
                    else:
                        logger.warning(f"Invalid quote item format from {exchange}: {item}")
            elif j is not None:
                 logger.warning(f"Unexpected data type received for {exchange} quotes: {type(j)}")
            else:
                logger.error(f"Failed to fetch or cache data for {exchange}")

        logger.info(f"Finished fetching daily quotes. Total new prices added: {new_prices_found}")

        if ticker in TodayPrices:
            return {"symbol": ticker, "price": TodayPrices[ticker]}
        else:
            logger.warning(f"Price not found for {ticker} after fetching.")
            return {"error": f"Price not found for ticker {ticker}"}


@mcp.resource("fmp://profile/{ticker}")
def get_profile(ticker: str) -> Dict[str, Any]:
    """
    Get the company profile information for a given stock ticker.
    Profile data is cached monthly.

    Args:
        ticker: The stock symbol (e.g., AAPL, MSFT).
    """
    logger.info(f"Resource request: fmp://profile/{ticker}")
    global savedProfile
    ticker = ticker.upper()

    # Attempt to initialize cache if empty
    if not savedProfile:
        if not initialize_saved_profile_cache():
             return {"error": f"Failed to initialize profile cache for ticker {ticker}"}

    if ticker in savedProfile:
        return savedProfile[ticker]
    else:
        # Attempt re-initialization in case cache expired or failed initially
        logger.warning(f"Profile for {ticker} not found in cache. Attempting cache refresh.")
        if initialize_saved_profile_cache() and ticker in savedProfile:
             return savedProfile[ticker]
        else:
             logger.error(f"Profile still not found for {ticker} after cache refresh.")
             # Optionally, try a direct API call as a fallback?
             # url = f"https://financialmodelingprep.com/api/v3/profile/{ticker}?apikey={FMP_API_KEY}"
             # profile_data = get_jsonparsed_data(url) ... etc
             return {"error": f"Profile not found for ticker {ticker}"}

# --- Resources derived from Profile ---

@mcp.resource("fmp://profile/{ticker}/description")
def get_description_resource(ticker: str) -> Dict[str, Optional[str]]:
    """Get the company description from its profile."""
    profile_data = get_profile(ticker)
    if "error" in profile_data:
        return {"error": profile_data["error"], "description": None}
    return {"description": profile_data.get('description')}

@mcp.resource("fmp://profile/{ticker}/marketcap")
def get_market_cap_resource(ticker: str) -> Dict[str, Optional[float]]:
    """Get the company market capitalization from its profile."""
    profile_data = get_profile(ticker)
    if "error" in profile_data:
        return {"error": profile_data["error"], "marketCap": None}
    try:
        # Return as number, not formatted string
        mkt_cap_str = profile_data.get('mktCap')
        if mkt_cap_str is not None and mkt_cap_str != "":
            return {"marketCap": float(mkt_cap_str)}
        else:
            return {"marketCap": None}
    except (ValueError, TypeError):
         logger.warning(f"Could not parse market cap for {ticker}: {profile_data.get('mktCap')}")
         return {"marketCap": None}


@mcp.resource("fmp://profile/{ticker}/employees")
def get_num_employees_resource(ticker: str) -> Dict[str, Optional[int]]:
    """Get the number of full-time employees from the company profile."""
    profile_data = get_profile(ticker)
    if "error" in profile_data:
        return {"error": profile_data["error"], "employees": None}
    try:
         # Return as number, not string
        employees_str = profile_data.get('fullTimeEmployees')
        if employees_str is not None and employees_str != "" and employees_str.isdigit():
             return {"employees": int(employees_str)}
        else:
             # Handle cases where FMP might return "None" as a string or empty string
             logger.debug(f"Non-numeric or missing employee count for {ticker}: '{employees_str}'")
             return {"employees": None}
    except (ValueError, TypeError):
         logger.warning(f"Could not parse employee count for {ticker}: {profile_data.get('fullTimeEmployees')}")
         return {"employees": None}

@mcp.resource("fmp://profile/{ticker}/industry")
def get_industry_resource(ticker: str) -> Dict[str, Optional[str]]:
    """Get the company's industry from its profile."""
    profile_data = get_profile(ticker)
    if "error" in profile_data:
        return {"error": profile_data["error"], "industry": None}
    return {"industry": profile_data.get('industry')}

# --- Financial Statements & Metrics (Tools) ---

def _get_financial_statement(ticker: str, statement_type: str, period: str, limit: int) -> Dict[str, Any]:
    """Helper to fetch financial statements."""
    ticker = ticker.upper()
    period = period.lower()
    valid_periods = ["annual", "quarter"] # FMP uses 'quarter', not 'quarterly' here
    if period not in valid_periods:
        return {"error": f"Invalid period '{period}'. Must be 'annual' or 'quarter'."}

    # Map statement type to API path component
    path_map = {
        "income": "income-statement",
        "balance": "balance-sheet-statement",
        "cashflow": "cash-flow-statement"
    }
    if statement_type not in path_map:
         return {"error": f"Invalid statement type '{statement_type}'."}
    path_component = path_map[statement_type]

    logger.info(f"Getting {statement_type} statement for {ticker} ({period}, limit={limit})")
    filename_prefix = f"{ticker}_{period}_{statement_type}_{limit}" # Include limit in cache key
    url = f"https://financialmodelingprep.com/api/v3/{path_component}/{ticker}?period={period}&limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch {statement_type} statement for {ticker}"}
    if isinstance(j, dict) and not j: # Handle empty dict response
        logger.warning(f"Received empty dict for {statement_type} statement {ticker} ({period})")
        return {"data": []} # Return empty list for consistency
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for {statement_type} statement {ticker}: {type(j)}")
        # Check if it's an FMP error message
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": f"Unexpected data format received for {statement_type} statement."}

    # FMP returns a list of statements
    return {"data": j}


@mcp.tool()
def get_income_statement(ticker: str, period: str = "annual", limit: int = 5) -> Dict[str, Any]:
    """
    Get income statements for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'annual'.
        limit: Number of past statements to retrieve. Defaults to 5.
    """
    return _get_financial_statement(ticker, "income", period, limit)

@mcp.tool()
def get_balance_sheet(ticker: str, period: str = "annual", limit: int = 5) -> Dict[str, Any]:
    """
    Get balance sheet statements for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'annual'.
        limit: Number of past statements to retrieve. Defaults to 5.
    """
    return _get_financial_statement(ticker, "balance", period, limit)

@mcp.tool()
def get_cash_flow(ticker: str, period: str = "annual", limit: int = 5) -> Dict[str, Any]:
    """
    Get cash flow statements for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'annual'.
        limit: Number of past statements to retrieve. Defaults to 5.
    """
    return _get_financial_statement(ticker, "cashflow", period, limit)

@mcp.tool()
def get_key_metrics(ticker: str, period: str = "quarter", limit: int = 20) -> Dict[str, Any]:
    """
    Get key metrics for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'quarter'.
        limit: Number of past periods to retrieve. Defaults to 20.
    """
    ticker = ticker.upper()
    period = period.lower()
    valid_periods = ["annual", "quarter"]
    if period not in valid_periods:
        return {"error": f"Invalid period '{period}'. Must be 'annual' or 'quarter'."}

    logger.info(f"Getting key metrics for {ticker} ({period}, limit={limit})")
    filename_prefix = f"{ticker}_keymetrics_{period}_{limit}"
    url = f"https://financialmodelingprep.com/api/v3/key-metrics/{ticker}?period={period}&limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch key metrics for {ticker}"}
    if isinstance(j, dict) and not j:
        logger.warning(f"Received empty dict for key metrics {ticker} ({period})")
        return {"data": []}
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for key metrics {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for key metrics."}

    return {"data": j}


@mcp.tool()
def get_key_metrics_ttm(ticker: str) -> Dict[str, Any]:
    """
    Get Trailing Twelve Months (TTM) key metrics for a ticker.

    Args:
        ticker: The stock symbol.
    """
    ticker = ticker.upper()
    logger.info(f"Getting key metrics TTM for {ticker}")
    filename_prefix = f"{ticker}_keymetricsttm"
    url = f"https://financialmodelingprep.com/api/v3/key-metrics-ttm/{ticker}?apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch key metrics TTM for {ticker}"}
    # TTM usually returns a list with a single dictionary inside
    if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict):
         return {"data": j[0]} # Return the dict directly
    elif isinstance(j, list) and len(j) == 0:
         logger.warning(f"Received empty list for key metrics TTM {ticker}")
         return {"data": {}}
    else:
        logger.error(f"Unexpected data format for key metrics TTM {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for key metrics TTM."}


@mcp.tool()
def get_financial_growth(ticker: str, period: str = "quarter", limit: int = 20) -> Dict[str, Any]:
    """
    Get financial growth metrics for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'quarter'.
        limit: Number of past periods to retrieve. Defaults to 20.
    """
    ticker = ticker.upper()
    period = period.lower()
    valid_periods = ["annual", "quarter"]
    if period not in valid_periods:
        return {"error": f"Invalid period '{period}'. Must be 'annual' or 'quarter'."}

    logger.info(f"Getting financial growth for {ticker} ({period}, limit={limit})")
    filename_prefix = f"{ticker}_financialgrowth_{period}_{limit}"
    url = f"https://financialmodelingprep.com/api/v3/financial-growth/{ticker}?period={period}&limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch financial growth for {ticker}"}
    if isinstance(j, dict) and not j:
        logger.warning(f"Received empty dict for financial growth {ticker} ({period})")
        return {"data": []}
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for financial growth {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for financial growth."}

    return {"data": j}


@mcp.tool()
def get_ratios(ticker: str, period: str = "quarter", limit: int = 20) -> Dict[str, Any]:
    """
    Get financial ratios for a ticker.

    Args:
        ticker: The stock symbol.
        period: Reporting period ('annual' or 'quarter'). Defaults to 'quarter'.
        limit: Number of past periods to retrieve. Defaults to 20 (note: FMP API might have different default/max).
    """
    ticker = ticker.upper()
    period = period.lower()
    valid_periods = ["annual", "quarter"]
    if period not in valid_periods:
        return {"error": f"Invalid period '{period}'. Must be 'annual' or 'quarter'."}

    logger.info(f"Getting ratios for {ticker} ({period}, limit={limit})")
    filename_prefix = f"{ticker}_ratios_{period}_{limit}"
    url = f"https://financialmodelingprep.com/api/v3/ratios/{ticker}?period={period}&limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch ratios for {ticker}"}
    if isinstance(j, dict) and not j:
        logger.warning(f"Received empty dict for ratios {ticker} ({period})")
        return {"data": []}
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for ratios {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for ratios."}

    return {"data": j}

@mcp.tool()
def get_ratios_ttm(ticker: str) -> Dict[str, Any]:
    """
    Get Trailing Twelve Months (TTM) financial ratios for a ticker.

    Args:
        ticker: The stock symbol.
    """
    ticker = ticker.upper()
    logger.info(f"Getting ratios TTM for {ticker}")
    filename_prefix = f"{ticker}_ratios-ttm"
    url = f"https://financialmodelingprep.com/api/v3/ratios-ttm/{ticker}?apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch ratios TTM for {ticker}"}
    if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict):
         return {"data": j[0]}
    elif isinstance(j, list) and len(j) == 0:
         logger.warning(f"Received empty list for ratios TTM {ticker}")
         return {"data": {}}
    else:
        logger.error(f"Unexpected data format for ratios TTM {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for ratios TTM."}

# --- Analyst & Competitor Data ---

@mcp.tool()
def get_analyst_estimates(ticker: str, period: str = "quarter", limit: int = 4) -> Dict[str, Any]:
    """
    Get analyst earnings estimates for a ticker.

    Args:
        ticker: The stock symbol.
        period: Period type ('annual' or 'quarter'). Defaults to 'quarter'.
        limit: Number of estimates periods to fetch. Defaults to 4.
    """
    ticker = ticker.upper()
    period = period.lower()
    if period not in ["annual", "quarter"]:
         return {"error": "Invalid period. Use 'annual' or 'quarter'."}

    logger.info(f"Getting analyst estimates for {ticker} ({period}, limit={limit})")
    filename_prefix = f"{ticker}_analystestimates_{period}_{limit}"
    url = f"https://financialmodelingprep.com/api/v3/analyst-estimates/{ticker}?period={period}&limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch analyst estimates for {ticker}"}
    if isinstance(j, dict) and not j:
        logger.warning(f"Received empty dict for analyst estimates {ticker} ({period})")
        return {"data": []}
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for analyst estimates {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for analyst estimates."}

    return {"data": j}

@mcp.tool()
def get_analyst_recommendations(ticker: str, limit: int = 20) -> Dict[str, Any]:
    """
    Get analyst stock recommendations (buy, hold, sell) for a ticker.

    Args:
        ticker: The stock symbol.
        limit: Number of recommendations to fetch. Defaults to 20.
    """
    ticker = ticker.upper()
    logger.info(f"Getting analyst recommendations for {ticker} (limit={limit})")
    filename_prefix = f"{ticker}_analystrecommendations_{limit}"
    url = f"https://financialmodelingprep.com/api/v3/analyst-stock-recommendations/{ticker}?limit={limit}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch analyst recommendations for {ticker}"}
    if isinstance(j, dict) and not j:
        logger.warning(f"Received empty dict for analyst recommendations {ticker}")
        return {"data": []}
    if not isinstance(j, list):
        logger.error(f"Unexpected data format for analyst recommendations {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for analyst recommendations."}

    return {"data": j}

@mcp.resource("fmp://competitors/{ticker}")
def get_competitors_resource(ticker: str) -> Dict[str, Any]:
    """
    Get a list of competitor stock symbols for a given ticker.

    Args:
        ticker: The stock symbol.
    """
    ticker = ticker.upper()
    logger.info(f"Getting competitors for {ticker}")
    filename_prefix = f"{ticker}_competitors"
    url = f"https://financialmodelingprep.com/api/v4/stock_peers?symbol={ticker}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch competitors for {ticker}", "competitors": []}
    # FMP API for peers returns a list containing one dictionary with 'symbol' and 'peersList'
    if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict) and 'peersList' in j[0]:
        return {"symbol": j[0].get('symbol'), "competitors": j[0]['peersList']}
    elif isinstance(j, list) and len(j) == 0:
         logger.warning(f"Received empty list for competitors {ticker}")
         return {"symbol": ticker, "competitors": []}
    else:
        logger.error(f"Unexpected data format for competitors {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"], "competitors": []}
        return {"error": "Unexpected data format received for competitors.", "competitors": []}

# --- SEC Filings & Transcripts ---

@mcp.tool()
def find_latest_sec_filing_links(ticker: str, filing_type: str, num_entries: int = 1) -> Dict[str, Any]:
    """
    Find links to the latest SEC filings for a ticker.

    Args:
        ticker: The stock symbol.
        filing_type: Type of filing (e.g., '10-K', '10-Q', '8-K', 'DEF 14A').
        num_entries: Number of latest filings to return links for. Defaults to 1.
    """
    ticker = ticker.upper()
    filing_type = filing_type.upper() # FMP API seems case-insensitive, but standardize anyway
    logger.info(f"Finding latest {num_entries} '{filing_type}' filing(s) for {ticker}")
    # No caching here as latest filings change frequently
    url = f"https://financialmodelingprep.com/api/v3/sec_filings/{ticker}?type={filing_type}&page=0&limit={num_entries}&apikey={FMP_API_KEY}"
    j = get_jsonparsed_data(url)

    if j is None:
        return {"error": f"Failed to fetch filings for {ticker}"}
    if isinstance(j, list):
        links = [f.get("finalLink") for f in j[:num_entries] if isinstance(f, dict) and "finalLink" in f]
        return {"filingType": filing_type, "links": links}
    else:
        logger.error(f"Unexpected data format for SEC filings {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": "Unexpected data format received for SEC filings."}

@mcp.tool()
def get_transcript(ticker: str, year: int, quarter: int) -> Dict[str, Any]:
    """
    Get the earnings call transcript for a specific ticker, year, and quarter.

    Args:
        ticker: The stock symbol.
        year: The year of the transcript.
        quarter: The quarter of the transcript (1, 2, 3, or 4).
    """
    ticker = ticker.upper()
    if quarter not in [1, 2, 3, 4]:
        return {"error": "Invalid quarter. Must be 1, 2, 3, or 4."}

    logger.info(f"Getting transcript for {ticker} ({year} Q{quarter})")
    filename_prefix = f"{ticker}_transcript_{year}_Q{quarter}"
    url = f"https://financialmodelingprep.com/api/v3/earning_call_transcript/{ticker}?year={year}&quarter={quarter}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch transcript for {ticker} {year} Q{quarter}"}
    # API returns a list, usually with one transcript if found
    if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict) and 'content' in j[0]:
         # Return the first transcript found
         transcript_data = j[0]
         return {
             "symbol": transcript_data.get('symbol'),
             "quarter": transcript_data.get('quarter'),
             "year": transcript_data.get('year'),
             "date": transcript_data.get('date'),
             "content": transcript_data.get('content')
         }
    elif isinstance(j, list) and len(j) == 0:
        # This typically means no transcript found for that period
        logger.warning(f"No transcript found for {ticker} {year} Q{quarter}")
        return {"error": f"No transcript found for {ticker} {year} Q{quarter}"}
    else:
        logger.error(f"Unexpected data format for transcript {ticker}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": f"Unexpected data format received for transcript."}

# --- Market Data & Utilities ---

@mcp.tool()
def get_tickers_list(min_market_cap: int = 100000000, min_avg_volume: int = 30000) -> Dict[str, Any]:
    """
    Get a list of actively traded US stock tickers meeting minimum market cap and volume criteria.

    Args:
        min_market_cap: Minimum market capitalization. Defaults to 100,000,000.
        min_avg_volume: Minimum average daily volume. Defaults to 30,000.
    """
    logger.info(f"Getting tickers list (minCap={min_market_cap}, minVol={min_avg_volume})")
    global savedProfile
    # Ensure profile cache is loaded
    if not savedProfile:
        if not initialize_saved_profile_cache():
             return {"error": "Failed to initialize profile cache to get tickers list"}

    tickers = []
    count_total = 0
    count_filtered = 0
    for k, v in savedProfile.items():
        count_total += 1
        try:
            # Check required fields exist and are not None/empty before attempting conversion
            mkt_cap_str = v.get('mktCap')
            vol_avg_str = v.get('VolAvg')
            country = v.get('country')
            exchange = v.get('exchangeShortName')
            is_etf_str = v.get('isEtf')
            is_fund_str = v.get('isFund')
            is_trading_str = v.get('isActivelyTrading')

            if (mkt_cap_str and vol_avg_str and country and exchange and
                is_etf_str is not None and is_fund_str is not None and is_trading_str is not None):

                mkt_cap = int(float(mkt_cap_str))
                avg_vol = int(float(vol_avg_str))
                is_etf = str(is_etf_str).lower() == 'true'
                is_fund = str(is_fund_str).lower() == 'true'
                is_trading = str(is_trading_str).lower() == 'true'

                if (mkt_cap > min_market_cap and
                    avg_vol > min_avg_volume and
                    country in ['US', "IL"] and # Keep IL? Or just US?
                    exchange in ['NASDAQ', 'NYSE', 'AMEX'] and
                    not is_etf and
                    not is_fund and
                    is_trading):
                    tickers.append(k)
                    count_filtered += 1

            # else: # Optional: Log why a ticker was skipped
            #     missing = [field for field in ['mktCap', 'VolAvg', 'country', 'exchangeShortName', 'isEtf', 'isFund', 'isActivelyTrading'] if not v.get(field)]
            #     if missing: logger.debug(f"Skipping {k} due to missing fields: {missing}")


        except (ValueError, TypeError) as e:
            logger.warning(f"Could not process profile data for ticker {k}: {e} - Data: {v}")
        except Exception as e:
             logger.error(f"Unexpected error processing profile for ticker {k}: {e}")


    logger.info(f"Filtered {count_filtered} tickers from {count_total} profiles.")
    return {"tickers": tickers}


@mcp.resource("fmp://treasury/10y/today")
def get_10_year_treasury_today_resource() -> Dict[str, Any]:
    """Get the latest available 10-year US Treasury yield."""
    logger.info("Getting today's 10-year treasury yield")
    # Look back 7 days to ensure we get the most recent trading day's data
    last_week = datetime.datetime.now() - datetime.timedelta(days=7)
    from_date = last_week.strftime("%Y-%m-%d")
    to_date = datetime.datetime.now().strftime("%Y-%m-%d")

    filename_prefix = f"10year_treasury_{to_date}" # Cache based on 'to_date'
    url = f"https://financialmodelingprep.com/api/v4/treasury?from={from_date}&to={to_date}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": "Failed to fetch 10-year treasury data"}
    if isinstance(j, list) and len(j) > 0 and isinstance(j[0], dict) and 'year10' in j[0]:
         # API returns list sorted reverse-chronologically, first item is latest
         latest_data = j[0]
         try:
             yield_val = float(latest_data['year10'])
             return {"date": latest_data.get('date'), "yield": yield_val}
         except (ValueError, TypeError):
             logger.error(f"Could not parse 10-year yield: {latest_data.get('year10')}")
             return {"error": "Could not parse 10-year yield"}
    else:
        logger.error(f"Unexpected data format for treasury data: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
             return {"error": j["Error Message"]}
        return {"error": "Unexpected data format or no data found for 10-year treasury"}

@mcp.resource("fmp://treasury/10y/{target_date}")
def get_10_year_treasury_on_date_resource(target_date: str) -> Dict[str, Any]:
    """
    Get the 10-year US Treasury yield for a specific date.
    Fetches data for a 14-day window around the target date to find the closest available data point.

    Args:
        target_date: The date in YYYY-MM-DD format.
    """
    logger.info(f"Getting 10-year treasury yield for {target_date}")
    try:
        date_obj = datetime.datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return {"error": "Invalid date format. Please use YYYY-MM-DD."}

    # Fetch data for a window around the target date
    week_before = date_obj - datetime.timedelta(days=7)
    week_after = date_obj + datetime.timedelta(days=7)
    from_date = week_before.strftime("%Y-%m-%d")
    to_date = week_after.strftime("%Y-%m-%d")

    filename_prefix = f"10year_treasury_window_{target_date}"
    url = f"https://financialmodelingprep.com/api/v4/treasury?from={from_date}&to={to_date}&apikey={FMP_API_KEY}"
    j = get_cached_fetch(url, filename_prefix)

    if j is None:
        return {"error": f"Failed to fetch 10-year treasury data around {target_date}"}

    if isinstance(j, list) and len(j) > 0:
        # Find the closest date <= target_date that has data
        closest_data = None
        min_delta = datetime.timedelta(days=100) # Large initial delta

        for item in j:
             if isinstance(item, dict) and 'date' in item and 'year10' in item:
                 try:
                     item_date = datetime.datetime.strptime(item['date'], "%Y-%m-%d").date()
                     delta = date_obj - item_date
                     # We want the latest date *on or before* the target_date
                     if delta >= datetime.timedelta(days=0) and delta < min_delta:
                         min_delta = delta
                         closest_data = item
                 except (ValueError, TypeError):
                     continue # Skip malformed entries

        if closest_data:
            try:
                 yield_val = float(closest_data['year10'])
                 return {"date": closest_data.get('date'), "yield": yield_val}
            except (ValueError, TypeError):
                 logger.error(f"Could not parse 10-year yield for {closest_data.get('date')}: {closest_data.get('year10')}")
                 return {"error": f"Could not parse 10-year yield for closest date {closest_data.get('date')}"}
        else:
             return {"error": f"No 10-year treasury data found on or before {target_date} within the fetched window"}
    else:
        logger.error(f"Unexpected data format for treasury data around {target_date}: {type(j)}")
        if isinstance(j, dict) and "Error Message" in j:
            return {"error": j["Error Message"]}
        return {"error": f"Unexpected data format or no data found for 10-year treasury around {target_date}"}

@mcp.tool()
def get_sec_filing_text(url: str) -> Dict[str, Any]:
    """
    Fetches the content of an SEC filing URL and converts it to plain text.

    Args:
        url: The direct URL to the SEC filing (usually ending in .htm).
    """
    logger.info(f"Getting SEC filing text from {url}")

    # Basic validation of URL structure (optional but helpful)
    if not url.startswith("http") or ".sec.gov/" not in url:
         return {"error": "Invalid SEC filing URL provided."}

    # Fetching the webpage content
    # SEC requires a specific User-Agent format
    user_agent = os.environ.get("SEC_ACCESS", "YourCompanyName YourEmail@example.com") # Get from env or use placeholder
    if user_agent == "YourCompanyName YourEmail@example.com":
        logger.warning("Using default placeholder SEC User-Agent. Set SEC_ACCESS environment variable (e.g., 'MyCompanyName my.email@domain.com') for compliance.")

    headers = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov"
    }
    try:
        response = requests.get(url, headers=headers, allow_redirects=True, timeout=30)
        response.raise_for_status() # Check for HTTP errors
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to fetch SEC filing from {url}: {e}")
        return {"error": f"Failed to fetch SEC filing URL: {e}"}

    # Convert HTML to text
    try:
        h = html2text.HTML2Text()
        h.ignore_links = True # Often not needed for analysis
        h.ignore_images = True
        h.body_width = 0 # Don't wrap lines

        # Decode using UTF-8, handle errors gracefully
        html_content = response.content.decode("utf-8", errors='replace')
        text = h.handle(html_content)

        # Basic cleaning (optional: remove excessive whitespace)
        # text = re.sub(r'\n\s*\n', '\n\n', text).strip() # Consolidate multiple blank lines

        return {"url": url, "text": text}

    except Exception as e:
        logger.error(f"Error converting HTML to text for {url}: {e}")
        return {"error": f"Error processing filing content: {e}"}


# --- Server Start ---

if __name__ == "__main__":
    if not FMP_API_KEY:
        # Redundant check, but ensures it doesn't try to run without the key if the initial exit failed
        logger.critical("FMP_API_KEY environment variable is not set. Server cannot start.")
        sys.exit(1)

    # Initialize profile cache at startup (optional, can be done lazily on first request)
    logger.info("Initializing profile cache at startup...")
    if not initialize_saved_profile_cache():
         logger.warning("Initial profile cache loading failed. Will attempt again on first request.")

    logger.info("Starting Financial Modeling Prep MCP Server...")
    try:
        # Start the MCP server using the simple run method
        # The MCP CLI tool typically handles host/port configuration
        mcp.run()
    except KeyboardInterrupt:
        logger.info("Server shutdown requested.")
    except Exception as e:
        logger.error(f"Server encountered an unexpected error: {e}", exc_info=True) # Log traceback
        sys.exit(1)