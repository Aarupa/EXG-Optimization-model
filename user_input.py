import pandas as pd
import os
from main import optimization_model

def get_file_path(prompt):
    while True:
        path = input(prompt)
        if path == '0':
            return None
        if os.path.isfile(path):
            return path
        else:
            print("File not found. Please enter a valid file path or 0 if not available.")

def get_float(prompt, default=None):
    while True:
        val = input(f"{prompt} [{default if default is not None else 'required'}]: ")
        if val == '' and default is not None:
            return default
        try:
            return float(val)
        except ValueError:
            print("Invalid input. Please enter a number.")

def get_profile_inputs(profile_type):
    profiles = []
    n = int(get_float(f"How many {profile_type} profiles? Enter 0 if none", default=0))
    for i in range(n):
        print(f"Enter details for {profile_type} profile #{i+1}:")
        path = get_file_path(f"  Path to {profile_type} profile Excel file: ")
        max_capacity = get_float(f"  Max capacity (MW): ")
        capital_cost = get_float(f"  Capital cost: ")
        marginal_cost = get_float(f"  Marginal cost: ")
        profiles.append({
            'path': path,
            'max_capacity': max_capacity,
            'capital_cost': capital_cost,
            'marginal_cost': marginal_cost
        })
    return profiles

def get_battery_inputs():
    batteries = []
    n = int(get_float("How many battery systems? Enter 0 if none", default=0))
    for i in range(n):
        print(f"Enter details for battery system #{i+1}:")
        capital_cost = get_float("  Capital cost: ")
        marginal_cost = get_float("  Marginal cost: ")
        efficiency = get_float("  Efficiency (0-1): ")
        DoD = get_float("  Depth of Discharge (0-1): ")
        batteries.append({
            'capital_cost': capital_cost,
            'marginal_cost': marginal_cost,
            'efficiency': efficiency,
            'DoD': DoD
        })
    return batteries

def main():
    print("=== EXG Optimizer User Input ===")
    demand_file = get_file_path("Enter path to consumer demand Excel file: ")
    OA_cost = get_float("Enter OA cost", default=1000)
    curtailment_selling_price = get_float("Enter curtailment selling price", default=3000)
    sell_curtailment_percentage = get_float("Enter sell curtailment percentage (0-1)", default=0.5)
    annual_curtailment_limit = get_float("Enter annual curtailment limit (0-1)", default=0.3)
    re_replacement = get_float("Enter RE replacement percentage (0-100)", default=65)
    peak_target = get_float("Enter RE replacement percentage for peak hours (0-100)", default=90) / 100
    peak_hours_input = input("Enter peak hours in 24h format, comma separated (e.g., 6,7,8,18,19,20): ")
    peak_hours = [int(h.strip()) for h in peak_hours_input.split(",") if h.strip().isdigit()]


    # Get user profiles
    solar_profiles = get_profile_inputs("solar")
    wind_profiles = get_profile_inputs("wind")
    battery_systems = get_battery_inputs()

    # Build input_data dict for optimizer
    input_data = {'IPP1': {}}
    if solar_profiles:
        input_data['IPP1']['Solar'] = {}
        for idx, s in enumerate(solar_profiles):
            profile_df = pd.read_excel(s['path'])
            input_data['IPP1']['Solar'][f'Solar_{idx+1}'] = {
                'profile': profile_df.squeeze(),
                'max_capacity': s['max_capacity'],
                'capital_cost': s['capital_cost'],
                'marginal_cost': s['marginal_cost']
            }
    if wind_profiles:
        input_data['IPP1']['Wind'] = {}
        for idx, w in enumerate(wind_profiles):
            profile_df = pd.read_excel(w['path'])
            input_data['IPP1']['Wind'][f'Wind_{idx+1}'] = {
                'profile': profile_df.squeeze(),
                'max_capacity': w['max_capacity'],
                'capital_cost': w['capital_cost'],
                'marginal_cost': w['marginal_cost']
            }
    if battery_systems:
        input_data['IPP1']['ESS'] = {}
        for idx, b in enumerate(battery_systems):
            input_data['IPP1']['ESS'][f'ESS_{idx+1}'] = {
                'capital_cost': b['capital_cost'],
                'marginal_cost': b['marginal_cost'],
                'efficiency': b['efficiency'],
                'DoD': b['DoD']
            }

    # Load demand data
    hourly_demand = pd.read_excel(demand_file)

    result = optimization_model(
        input_data=input_data,
        consumer_demand_path=demand_file,
        hourly_demand=hourly_demand,
        re_replacement=re_replacement,
        OA_cost=OA_cost,
        curtailment_selling_price=curtailment_selling_price,
        sell_curtailment_percentage=sell_curtailment_percentage,
        annual_curtailment_limit=annual_curtailment_limit,
        peak_target=peak_target,
        peak_hours=peak_hours
    )
    print("\n=== Optimization Result ===")
    print(result)

if __name__ == "__main__":
    main()
