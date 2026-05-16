import csv
import io
from typing import Any


def export_csv(headers: list[str], rows: list[list[Any]], filename: str = "export.csv") -> io.StringIO:
    output = io.StringIO()
    output.write("﻿")  # BOM for Excel UTF-8 compatibility
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    output.seek(0)
    return output
