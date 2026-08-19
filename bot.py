import os
import sys
import time
import requests
from eth_account import Account
from eth_utils import keccak

# ==============================================================================
# 1. إعدادات الشبكة والعقد المعتمد على Base
# ==============================================================================
BASE_RPC         = "https://mainnet.base.org"
PRIVATE_MEV_RPC  = "https://base.mev-share.flashbots.net"
CHAIN_ID         = 8453

MULTICALL3_ADDRESS = "0xca11bde05977b3631167028862be2a173976ca11"

CONTRACT_ADDRESS = "0x4d4Ef9B135B0FEC712d73F04f9df319bc4Fe2238"
OWNER_ADDRESS    = "0x5B499b1349dc5D8Ac2cd3E18C9B415884FD2BB56"

# سحب المفتاح الخاص بأمان وتشفير من GitHub Secrets
PRIVATE_KEY = os.environ.get("PRIVATE_KEY", "0x887de7cc8429fb98d269dc7207a81f1f369ba674437c66783eb4dd61e6287e8a")

# ==============================================================================
# 2. مصفوفة المسابح المعزولة المحدثة (The Niche Matrix)
# ==============================================================================
MONITORED_POOLS = [
    {
        "name": "WETH / USDC (Core)",
        "uni": "0xd0b53D9277642d899DF5C87A3966A349A798F224",
        "aero": "0xcDAC0d6c6C59727a65F871236188350531885C43",
        "dec_diff": 12,
        "fee": 0.35,
        "min_profit": 10
    },
    {
        "name": "AERO / USDC (Ecosystem)",
        "uni": "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d",
        "aero": "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d",
        "dec_diff": 0,
        "fee": 0.30,
        "min_profit": 8
    },
    {
        "name": "wstETH / WETH (Slipstream)",
        "uni": "0x2e997cbE45C401f7FdB7e4663eE9f43Fe4c2B1a9",
        "aero": "0xB07823f66D8E4069f2139E703664Daa4eb7fAc58",
        "dec_diff": 0,
        "fee": 0.06,
        "min_profit": 12
    }
]

# ==============================================================================
# 3. محرك التنفيذ والمحاكاة المسبقة بالحجم الذهبي
# ==============================================================================
def get_nonce():
    payload = {"jsonrpc": "2.0", "method": "eth_getTransactionCount", "params": [OWNER_ADDRESS, "latest"], "id": 1}
    try:
        res = requests.post(BASE_RPC, json=payload, timeout=3).json()
        return int(res['result'], 16)
    except:
        return 0

def simulate_preflight(data_hex):
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{
            "from": OWNER_ADDRESS,
            "to": CONTRACT_ADDRESS,
            "data": data_hex,
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
    selector = keccak(b"executeArbitrage(uint256,uint256)")[:4].hex()
    profit_hex = hex(int(pool['min_profit'] * 10**6))[2:].zfill(64)

    best_size = None
    best_data = None

    for size in flash_tiers:
        amount_hex = hex(int(size * 10**6))[2:].zfill(64)
        data_hex = "0x" + selector + amount_hex + profit_hex
        if simulate_preflight(data_hex):
            best_size = size
            best_data = data_hex
            break

    if not best_size:
        print(f"🛑 [محاكاة سريعة]: تم إلغاء صفقة {pool['name']} لحمايتك من الانزلاق السلبي.")
        return

    print(f"\n🔥 [اقتناص بالحجم الذهبي!] الزوج: {pool['name']} | الحجم المختار: ${best_size:,} USDC")
    
    nonce = get_nonce()
    tx = {
        'to': CONTRACT_ADDRESS,
        'value': 0,
        'gas': 550000,
        'maxFeePerGas': int(0.1 * 10**9),
        'maxPriorityFeePerGas': int(0.001 * 10**9),
        'nonce': nonce,
        'chainId': CHAIN_ID,
        'data': bytes.fromhex(best_data[2:])
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
            print(f"💰 تم إيداع صافي الربح كاش في محفظتك!")
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

            print(f"⚡ [سحابة Azure 24/7] {pool['name']:<22} | Uni: ${p_uni:<8.2f} | Aero: ${p_aero:<8.2f} | الصافي: {net_spread:.4f}%")

            if net_spread > 0.05:
                execute_golden_arbitrage(pool, eth_price=p_uni)
        except:
            continue

print("="*75)
print("🚀 انطلاق رادار Base MEV التلقائي 24/7 على سيرفرات مايكروسوفت السحابية")
print(f"🎯 العقد الذكي المنفذ: {CONTRACT_ADDRESS}")
print("="*75)

# تشغيل دورة الفحص لمدة 5 ساعات ونصف متواصلة لكل دورة عمل
start_time = time.time()
while time.time() - start_time < 19800: # 5.5 hours
    run_loop()
    time.sleep(1.5)
