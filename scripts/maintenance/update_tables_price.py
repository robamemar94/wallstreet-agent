import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# Pattern for the daily change cell in all tables
# It looks like:
# <td class="text-center fw-bold {% if dc > 0 %}text-success{% elif dc < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ dc }}">
#     {% if dc > 0 %}+{% endif %}{{ "%.2f"|format(dc) }}%
# </td>

old_pattern = r'''<td class="text-center fw-bold {% if dc > 0 %}text-success{% elif dc < 0 %}text-danger{% else %}text-muted{% endif %}" data-sort-value="{{ dc }}">
                                {% if dc > 0 %}\+{% endif %}{{ "%.2f"\|format\(dc\) }}%
                            </td>'''

new_content = r'''<td class="text-center" data-sort-value="{{ t.market_data.pct }}">
                                <div class="fw-bold text-dark">${{ "{:,.2f}".format(t.market_data.price) }}</div>
                                <div class="small fw-bold {% if t.market_data.change > 0 %}text-success{% elif t.market_data.change < 0 %}text-danger{% else %}text-muted{% endif %}">
                                    {% if t.market_data.change > 0 %}+{% endif %}{{ "{:,.2f}".format(t.market_data.change) }} 
                                    ({% if t.market_data.pct > 0 %}+{% endif %}{{ "{:.2f}%".format(t.market_data.pct) }})
                                </div>
                            </td>'''

# Use re.sub with some flexibility for spacing
content = re.sub(r'\{% set dc = t\.get\(\'daily_change\', 0\.0\) %\}\s*' + old_pattern, new_content, content)

# Also update the sorting logic which might rely on column indexes if they changed,
# but since we replaced in-place, the column index 4 should still be fine.

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated index.html table cells")
