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
        max_energy_capacity = get_float("  Max energy capacity (MWh): ")
        batteries.append({
            'capital_cost': capital_cost,
            'marginal_cost': marginal_cost,
            'efficiency': efficiency,
            'DoD': DoD,
            'max_energy_capacity': max_energy_capacity
        })
    return batteries

def apply_degradation(df, degradation_pct, years):
    """Apply yearly degradation across full hourly dataset."""
    frames = []
    for y in range(years):
        degraded_df = df * ((1 - (degradation_pct / 100)) ** y)
        degraded_df = degraded_df.reset_index(drop=True)
        frames.append(degraded_df)
    return pd.concat(frames, ignore_index=True)

def main():
    print("=== EXG Optimizer User Input ===")

    # === New Inputs ===
    PPA_capacity = get_float("Enter PPA Capacity (MW): ")
    PPA_tenure_years = int(get_float("Enter PPA Tenure (years): ", default=1))
    transmission_capacity = get_float("Enter Transmission Connectivity Capacity (MW): ")
    solar_degradation = get_float("Enter Solar degradation per year (%)", default=0.0)
    wind_degradation = get_float("Enter Wind degradation per year (%)", default=0.0)
    battery_degradation = get_float("Enter Battery degradation per year (%)", default=0.0)
    battery_max_hours = get_float("Enter Battery max hours", default=4)

    # === Demand Input ===
    demand_option = input("Do you have a demand Excel file? (y/n): ").strip().lower()
    if demand_option == 'y':
        demand_file = get_file_path("Enter path to consumer demand Excel file: ")
        hourly_demand = pd.read_excel(demand_file)
    else:
        print("\nNo demand file provided.")
        print(f"Using flat demand based on PPA Capacity = {PPA_capacity} MW for 24 hours.")
        hourly_demand = pd.DataFrame({'Demand': [PPA_capacity] * 24})
        demand_file = None

    # === Existing Inputs ===
    OA_cost = get_float("Enter OA cost", default=1000)
    curtailment_selling_price = get_float("Enter curtailment selling price", default=3000)
    sell_curtailment_percentage = get_float("Enter sell curtailment percentage (0-1)", default=0.5)
    annual_curtailment_limit = get_float("Enter annual curtailment limit (0-1)", default=0.3)
    re_replacement = get_float("Enter RE replacement percentage (0-100)", default=65)
    peak_target = get_float("Enter RE replacement percentage for peak hours (e.g., 0.9 for 90%)", default=0.9)
    peak_hours_input = input("Enter peak hours in 24h format, comma separated (e.g., 6,7,8,18,19,20): ")
    peak_hours = [int(h.strip()) for h in peak_hours_input.split(",") if h.strip().isdigit()] 

    # === Profile Inputs ===
    solar_profiles = get_profile_inputs("solar")
    wind_profiles = get_profile_inputs("wind")
    battery_systems = get_battery_inputs()

    # === Extend Demand for PPA Tenure Years ===
    hours_per_year = 24 * 365
    base_hours = len(hourly_demand)

    # If user provided only 24-hour data, expand for 1 year (365 days)
    if base_hours == 24:
        hourly_demand = pd.concat([hourly_demand] * 365, ignore_index=True)
        print(f"Base hourly demand expanded for 1 year = {len(hourly_demand)} rows")

    # Extend for full PPA tenure (e.g., 5 years → 43,800 hours)
    extended_demand = pd.concat([hourly_demand] * PPA_tenure_years, ignore_index=True)

    # Only add PPA capacity if actual demand file was provided
    if demand_option == 'y':
        extended_demand['Demand'] += PPA_capacity

    print(f"Total extended demand rows: {len(extended_demand)} (expected {hours_per_year * PPA_tenure_years})")

    # === Apply degradation and build input data ===
    input_data = {'IPP1': {}}
    
    if solar_profiles:
        input_data['IPP1']['Solar'] = {}
        for idx, s in enumerate(solar_profiles):
            base_df = pd.read_excel(s['path']).squeeze()
            # If solar profile has only 24 rows → expand to 8760×years
            if len(base_df) == 24:
                base_df = pd.concat([base_df] * hours_per_year, ignore_index=True)
            profile_df = apply_degradation(base_df, solar_degradation, PPA_tenure_years)
            input_data['IPP1']['Solar'][f'Solar_{idx+1}'] = {
                'profile': profile_df,
                'max_capacity': s['max_capacity'],
                'capital_cost': s['capital_cost'],
                'marginal_cost': s['marginal_cost']
            }

    if wind_profiles:
        input_data['IPP1']['Wind'] = {}
        for idx, w in enumerate(wind_profiles):
            base_df = pd.read_excel(w['path']).squeeze()
            if len(base_df) == 24:
                base_df = pd.concat([base_df] * hours_per_year, ignore_index=True)
            profile_df = apply_degradation(base_df, wind_degradation, PPA_tenure_years)
            input_data['IPP1']['Wind'][f'Wind_{idx+1}'] = {
                'profile': profile_df,
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
                'DoD': b['DoD'],
                'max_energy_capacity': b['max_energy_capacity'],
                'battery_degradation': battery_degradation,
                'max_hours': battery_max_hours
            }

    # === Save Extended Input Data ===
    extended_demand.to_excel("extended_demand.xlsx", index=False)
    print("\n✅ Extended demand file saved as 'extended_demand.xlsx'")

    # === Run Optimization ===
    result = optimization_model(
        input_data=input_data,
        consumer_demand_path=demand_file,
        hourly_demand=extended_demand,
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

    print("\n✅ You can download the generated input file: extended_demand.xlsx")

if __name__ == "__main__":
    main()
