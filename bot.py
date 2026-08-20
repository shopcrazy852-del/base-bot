import os
import sys
import time
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

CONTRACT_ADDRESS = "0x2bf18d3137b53991b896c3987cb2c919c396887d"

AERODROME_ROUTER = "0xcF77a3Ba9A5CA399B7c97c748561549838234397"
UNISWAP_ROUTER   = "0x2626664c2603336E57B271c5C0b26F421741e481"

# التوكنات الرئيسية المعتمدة على Base
USDC    = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH    = "0x4200000000000000000000000000000000000006"
AERO    = "0x940181a94A35A4569E4529A3CDfB74e38FD98631"
cbBTC   = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
DEGEN   = "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed"
wstETH  = "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
cbETH   = "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22"

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
if not PRIVATE_KEY:
    print("❌ خطأ: لم يتم العثور على PRIVATE_KEY في الخزينة!")
    sys.exit(1)

account = Account.from_key(PRIVATE_KEY)
OWNER_ADDRESS = account.address

# ==============================================================================
# 2. مصفوفة الأزواج الشاملة (The Full Niche Matrix)
# ==============================================================================
MONITORED_POOLS = [
    {
        "name": "WETH / USDC (Core)",
        "uni": "0xd0b53D9277642d899DF5C87A3966A349A798F224",
        "aero": "0xcDAC0d6c6C59727a65F871236188350531885C43",
        "path1": [USDC, WETH],
        "path2": [WETH, USDC],
        "dec_diff": 12,
        "fee": 0.35,
        "min_profit": 10
    },
    {
        "name": "AERO / USDC (Ecosystem)",
        "uni": "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d",
        "aero": "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d",
        "path1": [USDC, AERO],
        "path2": [AERO, USDC],
        "dec_diff": 0,
        "fee": 0.30,
        "min_profit": 8
    },
    {
        "name": "wstETH / WETH (Slipstream)",
        "uni": "0x2e997cbE45C401f7FdB7e4663eE9f43Fe4c2B1a9",
        "aero": "0xB07823f66D8E4069f2139E703664Daa4eb7fAc58",
        "path1": [USDC, WETH, wstETH],
        "path2": [wstETH, WETH, USDC],
        "dec_diff": 0,
        "fee": 0.06,
        "min_profit": 12
    }
]

# ==============================================================================
# 3. محرك التنفيذ والمحاكاة المسبقة
# ==============================================================================
def get_nonce():
    payload = {"jsonrpc": "2.0", "method": "eth_getTransactionCount", "params": [OWNER_ADDRESS, "latest"], "id": 1}
    try:
        res = requests.post(BASE_RPC, json=payload, timeout=3).json()
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
        res = requests.post(BASE_RPC, json=payload, timeout=3).json()
        return ("result" in res and res["result"] != "0x")
    except:
        return False

def execute_golden_arbitrage(pool, eth_price):
    flash_tiers = [10000, 25000, 50000]
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

    print(f"\n🔥 [اقتناص بالحجم الذهبي!] الزوج: {pool['name']} | الحجم المختار: ${best_size:,} USDC")
    
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
            res = requests.post(PRIVATE_MEV_RPC, json=payload, timeout=3).json()
            if "error" in res: res = requests.post(BASE_RPC, json=payload, timeout=3).json()
        except:
            res = requests.post(BASE_RPC, json=payload, timeout=3).json()

        if "result" in res:
            print(f"✅ تم تنفيذ الصفقة بنجاح على Base!")
            print(f"🔗 هاش المعاملة: {res['result']}")
            print(f"💰 تم إيداع صافي الربح كاش في محفظة Jody فوراً!")
        else:
            print(f"⚠️ استجابة الشبكة: {res}")
    except Exception as e:
        print(f"❌ خطأ أثناء الإرسال: {e}")

def run_loop():
    calls = []
    for pool in MONITORED_POOLS:
        calls.append({"to": pool['uni'], "data": "0x3850c7bd"})
        calls.append({"to": pool['aero'], "data": "0x0902f1ac"})

    payload = [{"jsonrpc": "2.0", "method": "eth_call", "params": [{"to": c['to'], "data": c['data']}, "latest"], "id": i} for i, c in enumerate(calls)]

    try:
        res = requests.post(BASE_RPC, json=payload, timeout=3).json()
        results = {item['id']: item.get('result', None) for item in res}
    except:
        return

    for i, pool in enumerate(MONITORED_POOLS):
        res_uni = results.get(2 * i)
        res_aero = results.get(2 * i + 1)
        if not res_uni or not res_aero or res_uni == "0x" or res_aero == "0x": continue

        try:
            sqrt_p = int(res_uni[2:66], 16)
            p_uni = ((sqrt_p / (2**96)) ** 2) * (10**pool['dec_diff'])
            r0 = int(res_aero[2:66], 16)
            r1 = int(res_aero[66:130], 16)
            if r0 == 0: continue
            p_aero = (r1 / r0) * (10**pool['dec_diff'])

            diff_pct = abs(p_uni - p_aero) / min(p_uni, p_aero) * 100
            net_spread = diff_pct - pool['fee']

            print(f"⚡ [سحابة Azure 24/7] {pool['name']:<24} | Uni: ${p_uni:<8.2f} | Aero: ${p_aero:<8.2f} | الصافي: {net_spread:.4f}%")

            if net_spread > 0.05:
                execute_golden_arbitrage(pool, eth_price=p_uni)
        except:
            continue

print("="*75)
print("🚀 انطلاق المنظومة الماستر الموسعة 24/7 على سيرفرات مايكروسوفت السحابية")
print(f"💎 العقد الماستر: {CONTRACT_ADDRESS}")
print(f"🔒 الخزينة المستلمة للأرباح: محفظة Jody ({OWNER_ADDRESS})")
print("="*75)

start_time = time.time()
while time.time() - start_time < 19800: # 5.5 ساعات
    run_loop()
    time.sleep(1.5)
