import yaml

with open('config/agents.yaml', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f)

for agent_name, agent_data in data.items():
    if 'backstory' in agent_data:
        backstory = agent_data['backstory']
        
        # We need to split backstory into backstory and methodology
        if 'METODOLOGÍA' in backstory:
            parts = backstory.split('METODOLOGÍA', 1)
            agent_data['backstory'] = parts[0].strip()
            agent_data['methodology'] = 'METODOLOGÍA' + parts[1]
        elif 'PRINCIPIO FUNDAMENTAL:' in backstory:
            parts = backstory.split('PRINCIPIO FUNDAMENTAL:', 1)
            agent_data['backstory'] = parts[0].strip()
            agent_data['methodology'] = 'PRINCIPIO FUNDAMENTAL:\n' + parts[1]

with open('config/agents.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
