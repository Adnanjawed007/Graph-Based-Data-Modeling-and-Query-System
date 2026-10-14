"""Turn retrieved O2C evidence into concise, user-facing answers."""

def narrate_answer(fallback: str, plan: dict, evidence: dict | None) -> str:
    if not evidence:
        return fallback

    intent = plan.get("intent")
    if intent == "document_trace":
        nodes = evidence.get("nodes", [])
        root_id = evidence.get("root")
        root = next((node for node in nodes if node.get("id") == root_id), {})
        kind = plan.get("document_type", "document").replace("_", " ")
        identifier = plan.get("document_id", "")
        data = root.get("data", {})
        if kind == "billing document":
            entries = sorted({str(node.get("identifier")) for node in nodes if node.get("type") == "journal_entry" and node.get("identifier")})
            if entries:
                linked = ", ".join(entries)
                return f"Billing document {identifier} is linked to journal entr{'y' if len(entries) == 1 else 'ies'} {linked}. The trace contains {len(nodes)} connected records."
        if kind == "sales order":
            parts = [f"Sales order {identifier}"]
            created = str(data.get("creationDate") or "")[:10]
            customer = data.get("soldToParty")
            amount = data.get("totalNetAmount")
            currency = data.get("transactionCurrency")
            if created:
                parts.append(f"created on {created}")
            if customer:
                parts.append(f"for customer {customer}")
            if amount is not None:
                parts.append(f"with a total net amount of {amount} {currency or ''}".strip())
            summary = " was " + ", ".join(parts[1:]) if len(parts) > 1 else " was found"
            related = {}
            for node in nodes:
                typ = node.get("type")
                if typ in {"delivery", "billing_document", "journal_entry"} and node.get("identifier"):
                    related.setdefault(typ, set()).add(str(node["identifier"]))
            links = []
            for typ, label in (("delivery", "delivery"), ("billing_document", "billing document"), ("journal_entry", "journal entry")):
                ids = sorted(related.get(typ, set()))
                if ids:
                    links.append(f"{len(ids)} {label}{'s' if len(ids) != 1 else ''} ({', '.join(ids[:5])}{'?' if len(ids) > 5 else ''})")
            suffix = (" It is connected to " + "; ".join(links) + ".") if links else ""
            return f"{''.join(parts[:1])}{summary}. The trace contains {len(nodes)} connected records.{suffix}"
        return f"Found {len(nodes)} connected records for {kind} {identifier}. The linked records are shown in the graph and retrieved evidence."

    if intent == "top_products":
        rows = evidence.get("results", [])
        if not rows:
            return "No matching billed products were found for those filters."
        metric = plan.get("rank_by", "billing_document_count")
        label, field = {"billing_document_count": ("non-cancelled billing documents", "billing_documents"), "net_amount": ("net amount", "net_amount"), "quantity": ("billed quantity", "quantity")}.get(metric, ("billing documents", "billing_documents"))
        examples = []
        for row in rows[:3]:
            desc = row.get("description") or {}
            name = next((desc.get(key) for key in ("productDescription", "productName", "description") if desc.get(key)), None) if isinstance(desc, dict) else None
            product = f"{name} (product {row.get('product')})" if name else f"product {row.get('product')}"
            value = row.get(field, "")
            if metric == "net_amount": value = f"{value} {row.get('currency', '')}".strip()
            examples.append(f"{product}: {value}")
        return f"Ranked {len(rows)} products by {label}. Top results: " + "; ".join(examples) + "."

    if intent == "customer_orders":
        rows = evidence.get("results", [])
        total = evidence.get("count", len(rows))
        if not rows:
            return f"No sales orders were found for customer {evidence.get('customer_id', '')} with those filters."
        sample = ", ".join(str(row.get("salesOrder")) for row in rows[:5] if row.get("salesOrder"))
        return f"Found {total} sales orders for customer {evidence.get('customer_id', '')}. Recent orders include {sample}."

    if intent == "billing_summary":
        rows = evidence.get("results", [])
        if not rows:
            return "No billing records matched those filters."
        field = plan.get("metric") or "net_amount"
        label = {"net_amount": "net amount", "quantity": "billed quantity", "document_count": "distinct billing documents"}.get(field, field)
        group = plan.get("group_by", "group")
        examples = [f"{row.get('group')}: {row.get(field)} {row.get('currency', '')}".strip() for row in rows[:3]]
        return f"Billing summary by {group}, ranked by {label}. Top results: " + "; ".join(examples) + "."

    if intent == "incomplete_flows":
        counts = evidence.get("counts", {})
        return (f"The checks found {counts.get('orders_without_delivery', 0)} sales orders without a referenced delivery "
                f"and {counts.get('deliveries_without_billing', 0)} deliveries without a matching non-cancelled billing item.")
    return fallback
