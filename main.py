import pypsa
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('debug_logger')

def optimization_model(input_data, consumer_demand_path=None, hourly_demand=None, 
                      OA_cost=None, curtailment_selling_price=None, 
                      sell_curtailment_percentage=None, annual_curtailment_limit=None, 
                      peak_target=None, peak_hours=None, Transmission_Connectivity=None):
    
    # Load demand data
    if consumer_demand_path is not None:
        demand_file = pd.read_excel(consumer_demand_path)
        if not isinstance(demand_file.index, pd.DatetimeIndex):
            demand_file.index = pd.date_range(start='2022-01-01', periods=len(demand_file), freq='h')
        demand_data = demand_file.squeeze()
    else:
        if not isinstance(hourly_demand.index, pd.DatetimeIndex):
            hourly_demand.index = pd.date_range(start='2022-01-01', periods=len(hourly_demand), freq='h')
        demand_data = hourly_demand.squeeze()

    results_dict = {}
    final_dict = input_data
    
    for ipp in final_dict:
        solar_projects = final_dict[ipp].get('Solar', {})
        ess_projects = final_dict[ipp].get('ESS', {})

        if solar_projects and ess_projects:
            for solar_project in solar_projects:
                solar_profile = solar_projects[solar_project]['profile']
                if not isinstance(solar_profile.index, pd.DatetimeIndex):
                    solar_profile.index = demand_data.index

                Solar_captialCost = solar_projects[solar_project]['capital_cost']
                Solar_marginalCost = solar_projects[solar_project]['marginal_cost']
                Solar_maxCapacity = solar_projects[solar_project]['max_capacity']
                solar_name = solar_project

                for ess_system in ess_projects:
                    Battery_captialCost = ess_projects[ess_system]['capital_cost']
                    Battery_marginalCost = ess_projects[ess_system]['marginal_cost']
                    Battery_Eff_store = ess_projects[ess_system]['efficiency']
                    Battery_Eff_dispatch = ess_projects[ess_system]['efficiency']
                    DoD = ess_projects[ess_system]['DoD']
                    Battery_max_energy_capacity = ess_projects[ess_system].get('max_energy_capacity', None)
                    ess_name = ess_system

                    # Stage 1: Daily optimization to determine battery sizing and daily energy requirements
                    daily_results = daily_optimization(
                        demand_data=demand_data,
                        solar_profile=solar_profile,
                        Solar_maxCapacity=Solar_maxCapacity,
                        peak_target=peak_target,
                        peak_hours=peak_hours,
                        Battery_Eff_dispatch=Battery_Eff_dispatch,
                        DoD=DoD
                    )

                    # Save daily results to Excel file
                    daily_df = pd.DataFrame({
                        'Date': daily_results['dates'],
                        'Daily Demand': daily_results['daily_demand'],
                        'Daily Solar Generation': daily_results['daily_solar_generation'],
                        'Daily Peak Demand': daily_results['daily_peak_demand'],
                        'Required Battery Discharge': daily_results['required_battery_discharge'],
                        'Required Battery Charge': daily_results['required_battery_charge'],
                        'Daily Battery Capacity': daily_results['daily_battery_capacity']
                    })
                    daily_filename = f"optimization_daily_results_{ipp}_{solar_name}_{ess_name}.xlsx"
                    daily_df.to_excel(daily_filename, index=False)

                    # Stage 2: Hourly optimization with daily constraints
                    network = setup_network_with_daily_constraints(
                        demand_data=demand_data,
                        solar_profile=solar_profile,
                        Solar_maxCapacity=Solar_maxCapacity,
                        Solar_captialCost=Solar_captialCost,
                        Solar_marginalCost=Solar_marginalCost,
                        Battery_captialCost=Battery_captialCost,
                        Battery_marginalCost=Battery_marginalCost,
                        Battery_Eff_store=Battery_Eff_store,
                        Battery_Eff_dispatch=Battery_Eff_dispatch,
                        ess_name=ess_name,
                        solar_name=solar_name,
                        Battery_max_energy_capacity=Battery_max_energy_capacity,
                        daily_constraints=daily_results
                    )

                    m = optimize_network_hourly_with_daily_constraints(
                        network=network,
                        solar_profile=solar_profile,
                        demand_data=demand_data,
                        Solar_maxCapacity=Solar_maxCapacity,
                        Solar_captialCost=Solar_captialCost,
                        Battery_captialCost=Battery_captialCost,
                        Solar_marginalCost=Solar_marginalCost,
                        Battery_marginalCost=Battery_marginalCost,
                        sell_curtailment_percentage=sell_curtailment_percentage,
                        curtailment_selling_price=curtailment_selling_price,
                        DoD=DoD,
                        annual_curtailment_limit=annual_curtailment_limit,
                        ess_name=ess_name,
                        peak_target=peak_target,
                        peak_hours=peak_hours,
                        Battery_max_energy_capacity=Battery_max_energy_capacity,
                        Transmission_Connectivity=Transmission_Connectivity,
                        daily_constraints=daily_results
                    )

                    analyze_network_results(
                        network=network,
                        sell_curtailment_percentage=sell_curtailment_percentage,
                        curtailment_selling_price=curtailment_selling_price,
                        solar_profile=solar_profile,
                        results_dict=results_dict,
                        OA_cost=OA_cost,
                        ess_name=ess_name,
                        solar_name=solar_name,
                        ipp_name=ipp,
                        daily_constraints=daily_results  # Pass daily results for reporting
                    )

    if results_dict:
        res_df = pd.DataFrame.from_dict(results_dict, orient='index')
        sorted_results = res_df.sort_values(by='Per Unit Cost')
        sorted_dict = sorted_results.to_dict(orient="index")
        return sorted_dict
    else:
        return {"error": "The demand cannot be met by the IPPs"}

def daily_optimization(demand_data, solar_profile, Solar_maxCapacity, peak_target, 
                      peak_hours, Battery_Eff_dispatch, DoD):
    """
    Stage 1: Daily-level optimization to determine:
    - Required battery capacity per day
    - Daily charge/discharge requirements
    - Peak demand coverage
    """
    
    # Group data by day
    daily_demand = demand_data.groupby(demand_data.index.date).sum()
    daily_solar = solar_profile.groupby(solar_profile.index.date).sum() * Solar_maxCapacity
    
    # Calculate peak demand per day
    peak_mask = demand_data.index.hour.isin(peak_hours)
    daily_peak_demand = demand_data[peak_mask].groupby(demand_data[peak_mask].index.date).sum()
    
    # Calculate required battery discharge per day
    daily_solar_peak = solar_profile[peak_mask].groupby(solar_profile[peak_mask].index.date).sum() * Solar_maxCapacity
    required_battery_discharge = (daily_peak_demand * peak_target - daily_solar_peak).clip(lower=0)
    
    # Calculate required battery charging per day (accounting for efficiency)
    required_battery_charge = required_battery_discharge / Battery_Eff_dispatch
    
    # Determine battery capacity requirements per day
    daily_battery_capacity = required_battery_charge / (1 - DoD)  # Account for Depth of Discharge
    
    daily_results = {
        'dates': list(daily_demand.index),
        'daily_demand': daily_demand.values,
        'daily_solar_generation': daily_solar.values,
        'daily_peak_demand': daily_peak_demand.reindex(daily_demand.index, fill_value=0).values,
        'required_battery_discharge': required_battery_discharge.reindex(daily_demand.index, fill_value=0).values,
        'required_battery_charge': required_battery_charge.reindex(daily_demand.index, fill_value=0).values,
        'daily_battery_capacity': daily_battery_capacity.reindex(daily_demand.index, fill_value=0).values
    }
    
    return daily_results

def setup_network_with_daily_constraints(demand_data, solar_profile, Solar_maxCapacity, 
                                        Solar_captialCost, Solar_marginalCost,
                                        Battery_captialCost, Battery_marginalCost,
                                        Battery_Eff_store, Battery_Eff_dispatch,
                                        ess_name, solar_name, Battery_max_energy_capacity,
                                        daily_constraints):
    """
    Setup network with daily constraint information
    """
    network = pypsa.Network()
    snapshots = demand_data.index
    network.set_snapshots(snapshots)

    # Add bus
    network.add("Bus", "ElectricityBus", carrier="AC")

    # Add demand
    network.add("Load", "ElectricityDemand", bus="ElectricityBus", p_set=demand_data.squeeze())

    # Add solar generator
    if solar_name is not None:
        network.add("Generator", "Solar", bus="ElectricityBus",
                   p_nom_extendable=True,
                   p_nom_max=Solar_maxCapacity,
                   capital_cost=Solar_captialCost,
                   marginal_cost=Solar_marginalCost,
                   p_max_pu=solar_profile.squeeze())

    # Add battery storage
    if ess_name is not None:
        network.add("StorageUnit", "Battery", bus="ElectricityBus",
                   p_nom_extendable=True,
                   capital_cost=Battery_captialCost,
                   marginal_cost=Battery_marginalCost,
                   efficiency_store=Battery_Eff_store,
                   efficiency_dispatch=Battery_Eff_dispatch,
                   max_hours=Battery_max_energy_capacity)

    # Add unmet demand generator
    network.add("Generator", "Unmet_Demand", bus="ElectricityBus",
               p_nom=1e6, marginal_cost=1e6, carrier="Unmet_Demand")

    # Store daily constraints in network for later use
    network.daily_constraints = daily_constraints

    return network

def optimize_network_hourly_with_daily_constraints(network, solar_profile, demand_data,
                                                  Solar_maxCapacity, Solar_captialCost,
                                                  Battery_captialCost, Solar_marginalCost,
                                                  Battery_marginalCost, sell_curtailment_percentage,
                                                  curtailment_selling_price, DoD, annual_curtailment_limit,
                                                  ess_name, peak_target, peak_hours, 
                                                  Battery_max_energy_capacity, Transmission_Connectivity,
                                                  daily_constraints):
    """
    Hourly optimization with daily constraints
    """
    m = network.optimize.create_model()
    
    # Add curtailment variables
    m.add_variables(lower=0, dims=["snapshot"], 
                   coords={"snapshot": network.snapshots},
                   name="Solar_curtailment")
    
    # Solar curtailment calculation
    def solar_curtailment_calculation(s):
        solar_generation = m.variables["Generator-p_nom"].loc["Solar"] * network.generators_t.p_max_pu["Solar"]
        solar_allocation = m.variables["Generator-p"].loc[s, "Solar"]
        constraint_expr = m.variables['Solar_curtailment'] == (solar_generation - solar_allocation)
        m.add_constraints(constraint_expr, name="solar_curtailment_calculation_constraint")
    
    solar_curtailment_calculation(network.snapshots)

    # Add final curtailment variable for objective
    m.add_variables(lower=0, dims=["snapshot"], 
                   coords={"snapshot": network.snapshots},
                   name="Final_snapshot_curtailment")
    
    m.objective += m.variables['Final_snapshot_curtailment'].sum()

    # DAILY CONSTRAINTS - Key addition for daily-level optimization
    
    if ess_name is not None:
        # Constraint 1: Daily battery discharge must meet peak demand requirements
        dates = daily_constraints['dates']
        required_discharge = daily_constraints['required_battery_discharge']
        
        for i, date in enumerate(dates):
            date_mask = network.snapshots.to_series().dt.date == date
            date_indices = network.snapshots[date_mask]
            
            if len(date_indices) > 0 and required_discharge[i] > 0:
                # Total battery discharge for the day
                daily_discharge = m.variables["StorageUnit-p_dispatch"].loc[date_indices, 'Battery'].sum()
                # Constraint: Daily discharge >= required discharge
                m.add_constraints(daily_discharge >= required_discharge[i], 
                                name=f"daily_discharge_constraint_{date}")
        
        # Constraint 2: Daily state of charge cycle
        for i, date in enumerate(dates):
            date_mask = network.snapshots.to_series().dt.date == date
            date_indices = network.snapshots[date_mask]
            
            if len(date_indices) > 1:
                # SOC at start and end of day should be similar (cyclic constraint)
                soc_start = m.variables["StorageUnit-state_of_charge"].loc[date_indices[0], 'Battery']
                soc_end = m.variables["StorageUnit-state_of_charge"].loc[date_indices[-1], 'Battery']
                
                # Allow small variation for operational flexibility
                m.add_constraints(soc_end >= soc_start * 0.9, 
                                name=f"daily_soc_cycle_constraint_{date}")
                m.add_constraints(soc_end <= soc_start * 1.1, 
                                name=f"daily_soc_cycle_constraint_upper_{date}")

    # PEAK HOUR CONSTRAINTS (existing functionality)
    def add_peak_hour_constraint(peak_target, peak_hours):
        if peak_target is None or peak_hours is None:
            return

        peak_mask = network.snapshots.to_series().dt.hour.isin(peak_hours)
        total_peak_demand = network.loads_t.p_set.loc[peak_mask, "ElectricityDemand"].sum()
        peak_indices = network.snapshots[peak_mask]
        unmet_peak = m.variables["Generator-p"].loc[peak_indices, 'Unmet_Demand'].sum()

        constraint_expr = unmet_peak <= (1 - peak_target) * total_peak_demand
        m.add_constraints(constraint_expr, name="peak_hour_demand_constraint")

    add_peak_hour_constraint(peak_target=peak_target, peak_hours=peak_hours)

    # Battery energy capacity constraint
    if ess_name is not None and Battery_max_energy_capacity is not None:
        max_energy = Battery_max_energy_capacity
        constraint_expr = m.variables["StorageUnit-state_of_charge"].loc[:, 'Battery'] <= m.variables["StorageUnit-p_nom"].loc['Battery'] * max_energy
        m.add_constraints(constraint_expr, name="battery_energy_capacity_constraint")

    # Final curtailment cost calculation
    def final_curtailment_cost_calculation(s):
        curtailment_marginal = (m.variables['Solar_curtailment'] * network.generators.at["Solar", "marginal_cost"])
        sell_curtailment = (sell_curtailment_percentage * m.variables['Solar_curtailment']) * curtailment_selling_price
        constraint_expr = m.variables['Final_snapshot_curtailment'] == (curtailment_marginal - sell_curtailment)
        m.add_constraints(constraint_expr, name="final_curtailment_cost_constraint")

    final_curtailment_cost_calculation(network.snapshots)

    # Annual curtailment limit
    def add_annual_curtailment_limit_constraint():
        annual_solar_curt = m.variables['Solar_curtailment'].sum()
        annual_gen = (m.variables["Generator-p_nom"].loc["Solar"] * network.generators_t.p_max_pu["Solar"]).sum()
        constraint_expr = annual_solar_curt <= annual_curtailment_limit * annual_gen
        m.add_constraints(constraint_expr, name="annual_curtailment_limit_constraint")

    add_annual_curtailment_limit_constraint()

    # Battery operational constraints
    if ess_name is not None:
        battery_store = m.variables["StorageUnit-p_store"].loc[:, "Battery"]
        solar_gen = m.variables["Generator-p"].loc[:, "Solar"]
        
        # Battery can only charge from solar
        m.add_constraints(battery_store <= solar_gen, name="battery_charge_from_solar_only")
        m.add_constraints(battery_store >= 0, name="battery_store_nonnegative")

        # Transmission connectivity constraint
        if Transmission_Connectivity is not None:
            m.add_constraints(solar_gen <= Transmission_Connectivity, name="transmission_connectivity_limit")

    return m

# Modified analyze function to include daily results
def analyze_network_results(network, sell_curtailment_percentage, curtailment_selling_price,
                           solar_profile, results_dict, OA_cost, ess_name, solar_name, 
                           ipp_name, daily_constraints=None):
    
    try:
        lopf_status = network.optimize.solve_model()
        if lopf_status[1] == "infeasible":
            raise ValueError("Optimization returned 'infeasible' status.")

        # ... (rest of your existing analyze_network_results function)
        # Add daily constraint analysis to the results
        
        if daily_constraints:
            # Calculate how well daily constraints were met
            daily_performance = analyze_daily_performance(network, daily_constraints)
            # Incorporate daily performance metrics into results_dict
            
        # Your existing results processing code here...
        
    except Exception as e:
        logger.debug(f"Error in analysis: {e}")

def analyze_daily_performance(network, daily_constraints):
    """
    Analyze how well daily constraints were met
    """
    performance = {}
    
    # Compare planned vs actual daily battery usage
    # This helps validate that the two-stage approach worked correctly
    
    return performance