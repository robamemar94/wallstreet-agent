import re

with open('templates/portfolio.html', 'r') as f:
    content = f.read()

# Replace the table section with Tabs
start_idx = content.find('<!-- Tabla de Posiciones -->')
end_idx = content.find('<!-- Modal para Editar Posición -->')

table_block = content[start_idx:end_idx]

# We want to wrap the existing table in a Tab 1 and add Tab 2 for Closed Positions
tabs_html = """
<!-- Tabs de Portfolio -->
<ul class="nav nav-tabs mb-3" id="portfolioTabs" role="tablist">
  <li class="nav-item" role="presentation">
    <button class="nav-link active fw-bold" id="active-tab" data-bs-toggle="tab" data-bs-target="#active-pane" type="button" role="tab" aria-controls="active-pane" aria-selected="true">💼 Posiciones Abiertas</button>
  </li>
  <li class="nav-item" role="presentation">
    <button class="nav-link fw-bold text-muted" id="closed-tab" data-bs-toggle="tab" data-bs-target="#closed-pane" type="button" role="tab" aria-controls="closed-pane" aria-selected="false">🔒 Posiciones Cerradas</button>
  </li>
</ul>

<div class="tab-content" id="portfolioTabsContent">
  <!-- Pestaña Posiciones Abiertas -->
  <div class="tab-pane fade show active" id="active-pane" role="tabpanel" aria-labelledby="active-tab" tabindex="0">
"""

tabs_html += table_block

tabs_html += """
  </div>
  
  <!-- Pestaña Posiciones Cerradas -->
  <div class="tab-pane fade" id="closed-pane" role="tabpanel" aria-labelledby="closed-tab" tabindex="0">
    <div class="table-responsive bg-white rounded shadow-sm" style="font-size: 0.95rem;">
        <table class="table table-hover align-middle mb-0">
            <thead class="table-light">
                <tr>
                    <th class="ps-3">Ticker</th>
                    <th class="text-end">Acciones Compradas</th>
                    <th class="text-end">Acciones Vendidas</th>
                    <th class="text-end">Precio Medio Venta</th>
                    <th class="text-end">Ingresos Venta</th>
                    <th class="text-end">Bº Realizado</th>
                    <th class="text-center">Ajustes</th>
                </tr>
            </thead>
            <tbody>
                {% for item in closed_portfolio %}
                <tr>
                    <td class="ps-3 fw-bold">
                        <a href="/ticker/{{ item.ticker }}" class="text-decoration-none text-dark">{{ item.ticker }}</a>
                    </td>
                    <td class="text-end">{{ item.total_shares_bought }}</td>
                    <td class="text-end">{{ item.total_shares_sold }}</td>
                    <td class="text-end">{{ "{:,.2f}".format(item.average_sell_price) }} {{ item.currency_symbol }}</td>
                    <td class="text-end">{{ "{:,.2f}".format(item.total_sell_revenue) }} {{ item.currency_symbol }}</td>
                    <td class="text-end fw-bold {% if item.realized_pnl_eur > 0 %}text-success{% elif item.realized_pnl_eur < 0 %}text-danger{% else %}text-muted{% endif %}">
                        {% if item.realized_pnl_eur > 0 %}+{% endif %}{{ "{:,.2f}".format(item.realized_pnl_eur) }} €<br>
                        <small style="font-size: 0.8rem;">({% if item.realized_pnl > 0 %}+{% endif %}{{ "{:,.2f}".format(item.realized_pnl) }} {{ item.currency_symbol }})</small>
                    </td>
                    <td class="text-center">
                        <button class="btn btn-sm btn-outline-danger ms-1 px-2 py-0" onclick="deletePosition('{{ item.ticker }}')" title="Eliminar Histórico">🗑️</button>
                    </td>
                </tr>
                {% else %}
                <tr>
                    <td colspan="7" class="text-center py-4 text-muted">
                        No hay posiciones cerradas en el histórico.
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
  </div>
</div>
"""

content = content[:start_idx] + tabs_html + content[end_idx:]

with open('templates/portfolio.html', 'w') as f:
    f.write(content)
print("Added Tabs successfully")
