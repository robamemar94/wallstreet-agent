import re

with open('templates/portfolio.html', 'r') as f:
    content = f.read()

# 1. Add IDs to tables
content = content.replace('<table class="table table-hover align-middle mb-0">', '<table class="table table-hover align-middle mb-0" id="ACTIVE_TABLE_ID">', 1)
content = content.replace('ACTIVE_TABLE_ID', 'activeTable')

content = content.replace('<table class="table table-hover align-middle mb-0">', '<table class="table table-hover align-middle mb-0" id="closedTable">', 1)

# 2. Add onclick to Active Table Headers
active_th_old = """            <tr>
                <th class="ps-3">Ticker</th>
                <th class="text-end">Acciones</th>
                <th class="text-end">P. Compra</th>
                <th class="text-end">P. Actual</th>
                <th class="text-end">C. Diario</th>
                <th class="text-end">Valor Total</th>
                <th class="text-end">Bº No Realizado</th>
                <th class="text-end">Bº (Cerrado + Divs)</th>
                <th class="text-center">Ajustes</th>
            </tr>"""

active_th_new = """            <tr style="cursor: pointer;" title="Haz clic en una cabecera para ordenar">
                <th class="ps-3" onclick="sortTable('activeTable', 0)">Ticker ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 1, true)">Acciones ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 2, true)">P. Compra ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 3, true)">P. Actual ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 4, true)">C. Diario ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 5, true)">Valor Total ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 6, true)">Bº No Realizado ↕</th>
                <th class="text-end" onclick="sortTable('activeTable', 7, true)">Bº (Cerrado + Divs) ↕</th>
                <th class="text-center">Ajustes</th>
            </tr>"""

content = content.replace(active_th_old, active_th_new)

# 3. Add data-sort-value to Active Table Rows
# We use regex to match the loop body and insert data-sort-value
active_row_old = r"""            <tr>\s*<td class="ps-3 fw-bold">\s*<a href="/portfolio/ticker/\{\{ item.ticker \}\}" class="text-decoration-none text-dark">\{\{ item.ticker \}\}</a>\s*</td>\s*<td class="text-end">\{\{ item.shares \}\}</td>\s*<td class="text-end">\{\{ "\{:,\.2f\}".format\(item.average_price\) \}\} \{\{ item.currency_symbol \}\}</td>\s*<td class="text-end fw-bold">\{\{ "\{:,\.2f\}".format\(item.current_price\) \}\} \{\{ item.currency_symbol \}\}</td>\s*<td class="text-end fw-bold \{% if item.daily_change_total_eur > 0 %\}text-success\{% elif item.daily_change_total_eur < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}">\s*\{% if item.daily_change_total_eur > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(item.daily_change_total_eur\) \}\} €<br>\s*<small style="font-size: 0.8rem;">\(\{% if item.daily_change_pct > 0 %\}\+\{% endif %\}\{\{ "\{:\.2f\}%".format\(item.daily_change_pct\) \}\}\)</small>\s*</td>\s*<td class="text-end fw-bold">\{\{ "\{:,\.2f\}".format\(item.current_value\) \}\} \{\{ item.currency_symbol \}\}</td>\s*<td class="text-end fw-bold \{% if item.return_abs > 0 %\}text-success\{% elif item.return_abs < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}">\s*\{% if item.return_abs > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(item.return_abs\) \}\} \{\{ item.currency_symbol \}\}<br>\s*<small style="font-size: 0.8rem;">\(\{% if item.return_pct > 0 %\}\+\{% endif %\}\{\{ "\{:\.2f\}%".format\(item.return_pct\) \}\}\)</small>\s*</td>\s*<td class="text-end fw-bold \{% if item.total_profit_closed > 0 %\}text-success\{% elif item.total_profit_closed < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}">\s*\{% if item.total_profit_closed > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(item.total_profit_closed\) \}\} \{\{ item.currency_symbol \}\}\s*</td>"""

active_row_new = """            <tr class="portfolio-row">
                <td class="ps-3 fw-bold" data-sort-value="{{ item.ticker }}">
                    <a href="/portfolio/ticker/{{ item.ticker }}" class="text-decoration-none text-dark">{{ item.ticker }}</a>
                </td>
                <td class="text-end" data-sort-value="{{ item.shares }}">{{ item.shares }}</td>
                <td class="text-end" data-sort-value="{{ item.average_price }}">{{ "{:,.2f}".format(item.average_price) }} {{ item.currency_symbol }}</td>
                <td class="text-end fw-bold" data-sort-value="{{ item.current_price }}">{{ "{:,.2f}".format(item.current_price) }} {{ item.currency_symbol }}</td>
                <td class="text-end fw-bold {% if item.daily_change_total_eur > 0 %}text-success{% elif item.daily_change_total_eur < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ item.daily_change_total_eur }}">
                    {% if item.daily_change_total_eur > 0 %}+{% endif %}{{ "{:,.2f}".format(item.daily_change_total_eur) }} €<br>
                    <small style="font-size: 0.8rem;">({% if item.daily_change_pct > 0 %}+{% endif %}{{ "{:.2f}%".format(item.daily_change_pct) }})</small>
                </td>
                <td class="text-end fw-bold" data-sort-value="{{ item.current_value }}">{{ "{:,.2f}".format(item.current_value) }} {{ item.currency_symbol }}</td>
                <td class="text-end fw-bold {% if item.return_abs > 0 %}text-success{% elif item.return_abs < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ item.return_abs }}">
                    {% if item.return_abs > 0 %}+{% endif %}{{ "{:,.2f}".format(item.return_abs) }} {{ item.currency_symbol }}<br>
                    <small style="font-size: 0.8rem;">({% if item.return_pct > 0 %}+{% endif %}{{ "{:.2f}%".format(item.return_pct) }})</small>
                </td>
                <td class="text-end fw-bold {% if item.total_profit_closed > 0 %}text-success{% elif item.total_profit_closed < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ item.total_profit_closed }}">
                    {% if item.total_profit_closed > 0 %}+{% endif %}{{ "{:,.2f}".format(item.total_profit_closed) }} {{ item.currency_symbol }}
                </td>"""

content = re.sub(active_row_old, active_row_new, content)

# 4. Add onclick to Closed Table Headers
closed_th_old = """                <tr>
                    <th class="ps-3">Ticker</th>
                    <th class="text-end">Acciones Compradas</th>
                    <th class="text-end">Acciones Vendidas</th>
                    <th class="text-end">Precio Medio Venta</th>
                    <th class="text-end">Ingresos Venta</th>
                    <th class="text-end">Bº (Cerrado + Divs)</th>
                    <th class="text-center">Ajustes</th>
                </tr>"""

closed_th_new = """                <tr style="cursor: pointer;" title="Haz clic en una cabecera para ordenar">
                    <th class="ps-3" onclick="sortTable('closedTable', 0)">Ticker ↕</th>
                    <th class="text-end" onclick="sortTable('closedTable', 1, true)">Acciones Compradas ↕</th>
                    <th class="text-end" onclick="sortTable('closedTable', 2, true)">Acciones Vendidas ↕</th>
                    <th class="text-end" onclick="sortTable('closedTable', 3, true)">Precio Medio Venta ↕</th>
                    <th class="text-end" onclick="sortTable('closedTable', 4, true)">Ingresos Venta ↕</th>
                    <th class="text-end" onclick="sortTable('closedTable', 5, true)">Bº (Cerrado + Divs) ↕</th>
                    <th class="text-center">Ajustes</th>
                </tr>"""

content = content.replace(closed_th_old, closed_th_new)

# 5. Add data-sort-value to Closed Table Rows
closed_row_old = r"""                <tr>\s*<td class="ps-3 fw-bold">\s*<a href="/portfolio/ticker/\{\{ item.ticker \}\}" class="text-decoration-none text-dark">\{\{ item.ticker \}\}</a>\s*</td>\s*<td class="text-end">\{\{ item.total_shares_bought \}\}</td>\s*<td class="text-end">\{\{ item.total_shares_sold \}\}</td>\s*<td class="text-end">\{\{ "\{:,\.2f\}".format\(item.average_sell_price\) \}\} \{\{ item.currency_symbol \}\}</td>\s*<td class="text-end">\{\{ "\{:,\.2f\}".format\(item.total_sell_revenue\) \}\} \{\{ item.currency_symbol \}\}</td>\s*<td class="text-end fw-bold \{% if item.realized_pnl_eur > 0 %\}text-success\{% elif item.realized_pnl_eur < 0 %\}text-danger\{% else %\}text-muted\{% endif %\}">\s*\{% if item.realized_pnl_eur > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(item.realized_pnl_eur\) \}\} €<br>\s*<small style="font-size: 0.8rem;">\(\{% if item.total_profit_closed > 0 %\}\+\{% endif %\}\{\{ "\{:,\.2f\}".format\(item.total_profit_closed\) \}\} \{\{ item.currency_symbol \}\}\)</small>\s*</td>"""

closed_row_new = """                <tr class="portfolio-row">
                    <td class="ps-3 fw-bold" data-sort-value="{{ item.ticker }}">
                        <a href="/portfolio/ticker/{{ item.ticker }}" class="text-decoration-none text-dark">{{ item.ticker }}</a>
                    </td>
                    <td class="text-end" data-sort-value="{{ item.total_shares_bought }}">{{ item.total_shares_bought }}</td>
                    <td class="text-end" data-sort-value="{{ item.total_shares_sold }}">{{ item.total_shares_sold }}</td>
                    <td class="text-end" data-sort-value="{{ item.average_sell_price }}">{{ "{:,.2f}".format(item.average_sell_price) }} {{ item.currency_symbol }}</td>
                    <td class="text-end" data-sort-value="{{ item.total_sell_revenue }}">{{ "{:,.2f}".format(item.total_sell_revenue) }} {{ item.currency_symbol }}</td>
                    <td class="text-end fw-bold {% if item.realized_pnl_eur > 0 %}text-success{% elif item.realized_pnl_eur < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ item.realized_pnl_eur }}">
                        {% if item.realized_pnl_eur > 0 %}+{% endif %}{{ "{:,.2f}".format(item.realized_pnl_eur) }} €<br>
                        <small style="font-size: 0.8rem;">({% if item.total_profit_closed > 0 %}+{% endif %}{{ "{:,.2f}".format(item.total_profit_closed) }} {{ item.currency_symbol }})</small>
                    </td>"""

content = re.sub(closed_row_old, closed_row_new, content)

# 6. Add the sortTable Javascript
js_sort = """
    let sortDirections = {};

    function sortTable(tableId, columnIndex, isNumeric = false) {
        const table = document.getElementById(tableId);
        const tbody = table.querySelector("tbody");
        const rows = Array.from(tbody.querySelectorAll("tr.portfolio-row"));

        if (rows.length === 0) return;

        const sortKey = tableId + "-" + columnIndex;
        if (sortDirections[sortKey] === undefined) {
            sortDirections[sortKey] = true;
        }

        const ascending = sortDirections[sortKey];
        
        rows.sort((a, b) => {
            let valA = a.cells[columnIndex].textContent.trim();
            let valB = b.cells[columnIndex].textContent.trim();

            if (a.cells[columnIndex].hasAttribute('data-sort-value')) {
                valA = a.cells[columnIndex].getAttribute('data-sort-value');
                valB = b.cells[columnIndex].getAttribute('data-sort-value');
            }

            if (isNumeric) {
                const numA = parseFloat(valA) || 0;
                const numB = parseFloat(valB) || 0;
                return ascending ? numA - numB : numB - numA;
            } else {
                return ascending ? valA.localeCompare(valB) : valB.localeCompare(valA);
            }
        });

        sortDirections[sortKey] = !ascending;
        
        rows.forEach(row => tbody.appendChild(row));
    }
"""

content = content.replace("let editModal;", js_sort + "\n    let editModal;")

with open('templates/portfolio.html', 'w') as f:
    f.write(content)
print("Sorting implementation complete")
