"""Find what causes repeated voucher POST calls."""
import os, re

logs_dir = "logs"
for f in sorted(os.listdir(logs_dir)):
    if not f.endswith(".log"):
        continue
    text = open(os.path.join(logs_dir, f), encoding="utf-8").read()
    voucher_posts = text.count("POST /ledger/voucher")
    if voucher_posts >= 3:
        basename = os.path.basename(f)[:65]
        print(f"\n{voucher_posts}x POST /ledger/voucher in {basename}")
        # Show each voucher POST and its result (next few lines)
        for m in re.finditer(r"(POST /ledger/voucher.*?)(\d{3})\s+(ERR|OK)", text):
            print(f"  -> {m.group(2)} {m.group(3)}")
        # Look for reject messages near voucher
        for m in re.finditer(r"\[reject\].*voucher.*", text):
            print(f"  REJECT: {m.group(0)[:120]}")
        # Look for validation errors
        for m in re.finditer(r"POST /ledger/voucher[\s\S]{0,500}?validationMessages.*?\[(.*?)\]", text):
            for vm in re.finditer(r'"message"\s*:\s*"([^"]*)"', m.group(1)):
                print(f"  VALIDATION: {vm.group(1)[:100]}")
