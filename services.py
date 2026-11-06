"""Business logic: prices, totals, and transaction records."""

from datetime import timedelta

from .db import get_db


def build_lines(parsed_items, locked_prices=None):
    """
    Attach unit prices and calculate transaction totals.

    Existing tank sizes keep their original transaction price.
    Newly added sizes use the current price list.
    """
    locked_prices = locked_prices or {}
    db = get_db()
    lines = []

    for item in parsed_items:
        size = item["size_kg"]

        if size in locked_prices:
            unit_price = locked_prices[size]
        else:
            row = db.execute(
                "SELECT price FROM price_list WHERE size_kg=?",
                (size,)
            ).fetchone()

            if not row:
                return None, None, (
                    f"{size:g} kg is not in the price list."
                )

            unit_price = row["price"]

        lines.append({
            "size_kg": size,
            "quantity": item["quantity"],
            "unit_price": unit_price,
            "subtotal": round(
                unit_price * item["quantity"], 2
            )
        })

    totals = {
        "tank_count": sum(
            line["quantity"] for line in lines
        ),
        "total_kg": round(
            sum(
                line["size_kg"] * line["quantity"]
                for line in lines
            ), 2
        ),
        "total": round(
            sum(line["subtotal"] for line in lines), 2
        )
    }

    return lines, totals, None


def save_lines(transaction_id, lines):
    db = get_db()

    db.execute(
        "DELETE FROM transaction_items WHERE transaction_id=?",
        (transaction_id,)
    )

    db.executemany(
        """
        INSERT INTO transaction_items
        (transaction_id, size_kg, quantity, unit_price)
        VALUES (?, ?, ?, ?)
        """,
        [
            (
                transaction_id,
                line["size_kg"],
                line["quantity"],
                line["unit_price"]
            )
            for line in lines
        ]
    )


def fetch_lines(ids):
    """Return transaction tank lines grouped by transaction ID."""
    if not ids:
        return {}

    marks = ",".join("?" * len(ids))

    rows = get_db().execute(
        f"""
        SELECT transaction_id, size_kg, quantity, unit_price
        FROM transaction_items
        WHERE transaction_id IN ({marks})
        ORDER BY size_kg
        """,
        list(ids)
    ).fetchall()

    output = {transaction_id: [] for transaction_id in ids}

    for row in rows:
        output[row["transaction_id"]].append({
            "size_kg": row["size_kg"],
            "quantity": row["quantity"],
            "unit_price": row["unit_price"],
            "subtotal": round(
                row["unit_price"] * row["quantity"], 2
            )
        })

    return output


def with_items(rows):
    result = [dict(row) for row in rows]

    by_id = fetch_lines([row["id"] for row in result])

    for row in result:
        customer = get_db().execute(
            """
            SELECT MIN(id)
            FROM transactions
            WHERE customer_name = ? COLLATE NOCASE
            """,
            (row["customer_name"],)
        ).fetchone()[0]

        row["customer_id"] = customer
        row["items"] = by_id[row["id"]]

    return result


def get_transaction(tid):
    row = get_db().execute(
        "SELECT * FROM transactions WHERE id=?",
        (tid,)
    ).fetchone()

    return with_items([row])[0] if row else None


def list_prices():
    rows = get_db().execute(
        """
        SELECT size_kg, price
        FROM price_list
        ORDER BY size_kg
        """
    ).fetchall()

    return [dict(row) for row in rows]


def day_summary(date):
    db = get_db()

    day = db.execute(
        """
        SELECT
            COUNT(DISTINCT customer_name) AS customers,
            COUNT(*) AS refills,
            COALESCE(SUM(total_kg), 0) AS kg,
            COALESCE(SUM(total), 0) AS sales
        FROM transactions
        WHERE substr(created_at, 1, 10)=?
        """,
        (date,)
    ).fetchone()

    by_status = db.execute(
        """
        SELECT
            payment_status,
            COUNT(*) AS customers,
            SUM(total) AS amount
        FROM transactions
        WHERE substr(created_at, 1, 10)=?
        GROUP BY payment_status
        ORDER BY payment_status
        """,
        (date,)
    ).fetchall()

    return {
        "date": date,
        "day": {
            key: round(day[key], 2)
            for key in day.keys()
        },
        "by_payment_status": [
            {
                "payment_status": row["payment_status"],
                "customers": row["customers"],
                "amount": round(row["amount"], 2)
            }
            for row in by_status
        ]
    }


def range_summary(start, end):
    """
    Return sales for every day in the selected date range,
    including days without transactions.
    """
    db = get_db()

    start_date = start.isoformat()
    end_date = end.isoformat()

    rows = db.execute(
        """
        SELECT
            substr(created_at, 1, 10) AS day,
            COUNT(DISTINCT customer_name) AS customers,
            COUNT(*) AS refills,
            SUM(total_kg) AS kg,
            SUM(total) AS sales
        FROM transactions
        WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
        GROUP BY day
        """,
        (start_date, end_date)
    ).fetchall()

    by_day = {row["day"]: row for row in rows}

    days = []
    current_day = start

    while current_day <= end:
        row = by_day.get(current_day.isoformat())

        days.append({
            "date": current_day.isoformat(),
            "customers": row["customers"] if row else 0,
            "refills": row["refills"] if row else 0,
            "kg": round(row["kg"], 2) if row else 0,
            "sales": round(row["sales"], 2) if row else 0
        })

        current_day += timedelta(days=1)

    by_status = db.execute(
        """
        SELECT
            payment_status,
            COUNT(*) AS customers,
            SUM(total) AS amount
        FROM transactions
        WHERE substr(created_at, 1, 10) BETWEEN ? AND ?
        GROUP BY payment_status
        ORDER BY payment_status
        """,
        (start_date, end_date)
    ).fetchall()

    return {
        "start": start_date,
        "end": end_date,
        "days": days,
        "total": {
            "customers": sum(
                day["customers"] for day in days
            ),
            "refills": sum(
                day["refills"] for day in days
            ),
            "kg": round(
                sum(day["kg"] for day in days), 2
            ),
            "sales": round(
                sum(day["sales"] for day in days), 2
            )
        },
        "by_payment_status": [
            {
                "payment_status": row["payment_status"],
                "customers": row["customers"],
                "amount": round(row["amount"], 2)
            }
            for row in by_status
        ]
    }