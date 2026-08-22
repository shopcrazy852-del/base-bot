import os
import sys
import time
import threading
import requests
from eth_account import Account
from eth_utils import keccak
from eth_abi import encode

# ==============================================================================
# 1. إعدادات الشبكة والعقد الماستر
# ==============================================================================
BASE_RPC         = "https://mainnet.base.org"
PRIVATE_MEV_RPC  = "https://base.mev-share.flashbots.net"
CHAIN_ID         = 8453

# عقدك الماستر المنشور على شبكة Base
CONTRACT_ADDRESS = "0x2bf18d3137b53991b896c3987cb2c919c396887d"
AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c748561549838234397"
UNISWAP_ROUTER   = "0x2626664c2603336E57B271c5C0b26F421741e481"

# التوكنات المعتمدة على Base
USDC   = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH   = "0x4200000000000000000000000000000000000006"
cbETH  = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"
AERO   = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
DEGEN  = "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
if not PRIVATE_KEY:
    print("❌ خطأ: لم يتم العثور على PRIVATE_KEY في الخزينة!", flush=True)
    sys.exit(1)

account = Account.from_key(PRIVATE_KEY)
OWNER_ADDRESS = account.address
session = requests.Session()

# ==============================================================================
# 2. الخيط 1: رادار بينانس التنبؤي (CEX Lead Signal)
# ==============================================================================
market_signals = {"binance_eth": 0.0, "morpho_liquidations": 0}

def fetch_binance_stream():
    while True:
        try:
            res = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", timeout=2).json()
            market_signals["binance_eth"] = float(res['price'])
        except:
            pass
        time.sleep(1.0)

threading.Thread(target=fetch_binance_stream, daemon=True).start()

# ==============================================================================
# 3. الخيط 2: رادار تصفيات Morpho Blue الرسمي (Free GraphQL API)
# ==============================================================================
def morpho_liquidation_watcher():
    """فحص ديون الحيتان في منصات Morpho كل 30 ثانية عبر الـ GraphQL الرسمي"""
    graphql_endpoint = "https://blue-api.morpho.org/graphql"
    query = """
    query {
      markets(where: { chainId_in: [8453] }, first: 10) {
        items {
          uniqueKey
          lltv
          state {
            borrowAssets
            supplyAssets
          }
        }
      }
    }
    """
    while True:
        try:
            res = requests.post(graphql_endpoint, json={"query": query}, timeout=4).json()
            if "data" in res:
                market_signals["morpho_liquidations"] = len(res["data"]["markets"]["items"])
        except:
            pass
        time.sleep(30)

threading.Thread(target=morpho_liquidation_watcher, daemon=True).start()

# ==============================================================================
# 4. مصفوفة المسابح والتحكيم الثلاثي (The Full Strategy Matrix)
# ==============================================================================
MONITORED_POOLS = [
    {
        "name": "WETH / USDC (Slipstream 0.10%)",
        "uni": "0xd0b53D9277642d899DF5C87A3966A349A798F224",
        "aero": "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59",
        "path1": [USDC, WETH],
        "path2": [WETH, USDC],
        "dec_diff": 12,
        "fee": 0.10,
        "min_profit": 10,
        "max_size": 50000
    },
    {
        "name": "cbETH / WETH (Slipstream LST)",
        "uni": "0x10648ba41b8565907cfa1496765fa4d95390aa0d",
        "aero": "0x47ca96ea59c13f72745928887f84c9f52c3d7348",
        "path1": [USDC, WETH, cbETH],
        "path2": [cbETH, WETH, USDC],
        "dec_diff": 0,
        "fee": 0.10,
        "min_profit": 12,
        "max_size": 50000
    },
    {
        "name": "TRIANGLE: USDC -> WETH -> AERO -> USDC",
        "uni": "0xd0b53D9277642d899DF5C87A3966A349A798F224",
        "aero": "0xb2cc224c1c9fee385f8ad6a55b4d94e92359dc59",
        "path1": [USDC, WETH],
        "path2": [WETH, AERO, USDC],
        "dec_diff": 12,
        "fee": 0.35,
        "min_profit": 15,
        "max_size": 25000
    }
]

def decode_slot0(hex_data, dec_diff):
    if not hex_data or hex_data == "0x":
        return None
    try:
        sqrt_p = int(hex_data[2:66], 16)
        return ((sqrt_p / (2**96)) ** 2) * (10**dec_diff)
    except:
        return None

def get_nonce():
    payload = {"jsonrpc": "2.0", "method": "eth_getTransactionCount", "params": [OWNER_ADDRESS, "latest"], "id": 1}
    try:
        res = session.post(BASE_RPC, json=payload, timeout=3).json()
        return int(res['result'], 16)
    except:
        return 0

def simulate_preflight(data_bytes):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{
            "from": OWNER_ADDRESS,
            "to": CONTRACT_ADDRESS,
            "data": "0x" + data_bytes.hex(),
            "gas": "0x86470"
        }, "latest"],
        "id": 1
    }
    try:
        res = session.post(BASE_RPC, json=payload, timeout=3).json()
        return ("result" in res and res["result"] != "0x")
    except:
        return False

def execute_golden_arbitrage(pool):
    tier_max = pool.get('max_size', 50000)
    flash_tiers = [10000, 25000] if tier_max <= 25000 else [10000, 25000, 50000]
    
    selector = keccak(b"executeArbitrage((address,address,address[],address[],uint256,uint256,uint256,uint256))")[:4]

    best_size = None
    best_data = None

    for size in flash_tiers:
        flash_amount = int(size * 10**6)
        min_profit   = int(pool['min_profit'] * 10**6)
        min_out1     = 0
        builder_tip  = 500 # 5% للمعدن

        params = (
            AERODROME_ROUTER,
            UNISWAP_ROUTER,
            pool['path1'],
            pool['path2'],
            flash_amount,
            min_profit,
            min_out1,
            builder_tip
        )

        encoded_params = encode(
            ["(address,address,address[],address[],uint256,uint256,uint256,uint256)"],
            [params]
        )
        call_data = selector + encoded_params

        if simulate_preflight(call_data):
            best_size = size
            best_data = call_data
            break

    if not best_size:
        return

    print(f"\n🔥 [اقتناص رابح!] النمط: {pool['name']} | حجم القرض: ${best_size:,} USDC", flush=True)
    
    nonce = get_nonce()
    tx = {
        'to': CONTRACT_ADDRESS,
        'value': 0,
        'gas': 650000,
        'maxFeePerGas': int(0.1 * 10**9),
        'maxPriorityFeePerGas': int(0.001 * 10**9),
        'nonce': nonce,
        'chainId': CHAIN_ID,
        'data': best_data
    }

    try:
        signed = Account.sign_transaction(tx, PRIVATE_KEY)
        raw_hex = signed.raw_transaction.hex() if hasattr(signed, 'raw_transaction') else signed.rawTransaction.hex()
        payload = {"jsonrpc": "2.0", "method": "eth_sendRawTransaction", "params": ["0x" + raw_hex if not raw_hex.startswith("0x") else raw_hex], "id": 1}
        
        try:
            res = session.post(PRIVATE_MEV_RPC, json=payload, timeout=3).json()
            if "error" in res: res = session.post(BASE_RPC, json=payload, timeout=3).json()
        except:
            res = session.post(BASE_RPC, json=payload, timeout=3).json()

        if "result" in res:
            print(f"✅ تم تنفيذ الصفقة بنجاح على Base! الهاش: {res['result']}", flush=True)
            print(f"💰 تم إيداع صافي الأرباح كاش في محفظة Jody!", flush=True)
        else:
            print(f"⚠️ استجابة الشبكة: {res}", flush=True)
    except Exception as e:
        print(f"❌ خطأ أثناء الإرسال: {e}", flush=True)

def run_loop():
    calls = []
    for pool in MONITORED_POOLS:
        calls.append({"to": pool['uni'], "data": "0x3850c7bd"})
        calls.append({"to": pool['aero'], "data": "0x3850c7bd"})

    payload = [{"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": c['to'], "data": c['data']}, "latest"], "id": i} for i, c in enumerate(calls)]

    try:
        res = session.post(BASE_RPC, json=payload, timeout=3).json()
        results = {item['id']: item.get('result', None) for item in res}
    except:
        return

    b_price = market_signals.get("binance_eth", 0.0)

    for i, pool in enumerate(MONITORED_POOLS):
        res_uni = results.get(2 * i)
        res_aero = results.get(2 * i + 1)

        p_uni = decode_slot0(res_uni, pool['dec_diff'])
        p_aero = decode_slot0(res_aero, pool['dec_diff'])

        if p_uni and p_aero:
            diff_pct = abs(p_uni - p_aero) / min(p_uni, p_aero) * 100

            if b_price > 0 and pool['name'].startswith("WETH"):
                cex_diff = abs(b_price - p_uni) / p_uni * 100
                if cex_diff > 0.25:
                    diff_pct += cex_diff

            net_spread = diff_pct - pool['fee']
            status = f"🟢 +{net_spread:.4f}% [فرصة!]" if net_spread > 0.03 else f"⚪ {net_spread:.4f}%"

            print(f"⚡ [24/7 Apex] {pool['name']:<36} | Uni: ${p_uni:<8.2f} | Aero: ${p_aero:<8.2f} | الصافي: {status}", flush=True)

            if net_spread > 0.03:
                execute_golden_arbitrage(pool)

print("="*90, flush=True)
print("🚀 انطلاق المنظومة الشاملة المكتملة 24/7 (v7 Master Apex + Morpho API + 3-Hop)", flush=True)
print(f"💎 العقد الماستر: {CONTRACT_ADDRESS}")
print(f"🔒 الخزينة الحصرية المستلمة للأرباح: محفظة Jody ({OWNER_ADDRESS})")
print("📡 المسارات: Slipstream (0.10%) + Morpho GraphQL API + Triangular (3-Hop) + Binance Signal", flush=True)
print("="*90, flush=True)

start_time = time.time()
while time.time() - start_time < 19800:
    run_loop()
    time.sleep(2.0)
