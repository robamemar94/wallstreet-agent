import re

with open('templates/index.html', 'r') as f:
    content = f.read()

# Helper to remove status column
def remove_status_col(block):
    # Remove header
    block = re.sub(r'<th class="text-center" onclick="sortTable\([^,]+, 5\)">Estado ↕</th>', '', block)
    
    # Remove the TD for status
    # It looks like:
    # <td class="text-center" data-sort-value="{{ t.status }}">
    #     {% if t.status == 'ACCEPTED' %}
    #     ...
    # </td>
    
    # We can match from <td ... data-sort-value="{{ t.status }}"> to </td>
    block = re.sub(r'<td class="text-center" data-sort-value="\{\{ t\.status \}\}">.*?</td>', '', block, flags=re.DOTALL)
    
    # We also need to decrement the onclick column indices for sorting!
    # Because we removed column 5, columns 6,7,8,9,10 become 5,6,7,8,9
    for i in range(10, 5, -1):
        block = block.replace(f", {i})", f", {i-1})")
        block = block.replace(f", {i}, true)", f", {i-1}, true)")
    
    return block

# Replace accepted table block
start_acc = content.find('id="acceptedTable"')
end_acc = content.find('id="acceptedBody"')
end_acc_table = content.find('</table>', end_acc)
acc_block = content[start_acc:end_acc_table]
new_acc_block = remove_status_col(acc_block)
content = content.replace(acc_block, new_acc_block)

# Replace rejected table block
start_rej = content.find('id="rejectedTable"')
end_rej = content.find('id="rejectedBody"')
end_rej_table = content.find('</table>', end_rej)
rej_block = content[start_rej:end_rej_table]
new_rej_block = remove_status_col(rej_block)
content = content.replace(rej_block, new_rej_block)

with open('templates/index.html', 'w') as f:
    f.write(content)
print("Updated table columns")
