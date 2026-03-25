import requests
import json

def fetch_valr_markets():
    try:
        response = requests.get("https://api.valr.com/v1/public/marketsummary")
        data = response.json()
        
        zar_pairs = []
        for pair in data:
            if pair['currencyPair'].endswith("ZAR"):
                zar_pairs.append(pair)
                
        # Sort by quoteVolume (ZAR volume)
        top_volume = sorted(zar_pairs, key=lambda x: float(x.get('quoteVolume') or 0), reverse=True)
        
        with open("valr_data.txt", "w") as f:
            f.write("--- Top 10 ZAR pairs by volume (24h) ---\n")
            for p in top_volume[:10]:
                f.write(f"{p['currencyPair']}: Price {p.get('lastTradedPrice', 0)} | 24h Change: {p.get('changeFromPrevious', 0)}% | Vol (ZAR): {p.get('quoteVolume', 0)}\n")
                
            # Sort by lowest price
            f.write("\n--- Cheapest ZAR pairs (Price < 5 ZAR, Volume > 100k ZAR) ---\n")
            liquid_cheap = [p for p in zar_pairs if float(p.get('quoteVolume') or 0) > 100000 and float(p.get('lastTradedPrice') or 999999) < 5.0]
            liquid_cheap_sorted = sorted(liquid_cheap, key=lambda x: float(x.get('lastTradedPrice') or 999999))
            for p in liquid_cheap_sorted:
                f.write(f"{p['currencyPair']}: Price {p.get('lastTradedPrice', 0)} | 24h Change: {p.get('changeFromPrevious', 0)}% | Vol (ZAR): {p.get('quoteVolume', 0)}\n")
            
            f.write("\n--- High Volatility ZAR pairs (Volume > 100k ZAR) ---\n")
            volatile = sorted([p for p in zar_pairs if float(p.get('quoteVolume') or 0) > 100000], key=lambda x: abs(float(x.get('changeFromPrevious') or 0)), reverse=True)
            for p in volatile[:10]:
                f.write(f"{p['currencyPair']}: Price {p.get('lastTradedPrice', 0)} | 24h Change: {p.get('changeFromPrevious', 0)}% | Vol (ZAR): {p.get('quoteVolume', 0)}\n")

    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    fetch_valr_markets()
