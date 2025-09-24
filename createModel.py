import logging
from linopy import LinearExpression
logger = logging.getLogger('debug_logger')  # Use the new debug logger

def optimize_network(network=None, solar_profile=None, wind_profile=None, demand_data=None,
                     Solar_maxCapacity=None, Wind_maxCapacity=None, Solar_captialCost=None,
                     Wind_captialCost=None, Battery_captialCost=None, Solar_marginalCost=None,
                     Wind_marginalCost=None, Battery_marginalCost=None, sell_curtailment_percentage=None,
                     curtailment_selling_price=None, DO=None, DoD=None, annual_curtailment_limit=None,
                     ess_name=None,  peak_target=None, peak_hours=None, Battery_max_energy_capacity=None):
    # ...existing code...

    solar_present = solar_profile is not None and not solar_profile.empty
    wind_present = wind_profile is not None and not wind_profile.empty

    m = network.optimize.create_model()
    if solar_present:
        m.add_variables(
          lower=0,
          dims=["snapshot"],
          coords={"snapshot": network.snapshots},
          name="Solar_curtailment"
      )
        def solar_curtailment_calculation(s):
            solar_generation = m.variables["Generator-p_nom"].loc["Solar"] * network.generators_t.p_max_pu["Solar"]
            # logger.debug(f"Solar  with generator p_nom: {solar_generation}")
            network_g = network.generators_t.p_max_pu["Solar"]
            # logger.debug(f"network generation:------------")
            # logger.debug(f"network generation: {network_g}")
            solar_allocation = m.variables["Generator-p"].loc[s, "Solar"]
            constraint_expr = m.variables['Solar_curtailment'] == (solar_generation - solar_allocation)
            m.add_constraints(constraint_expr, name="solar_curtailment_calculation_constraint")

        solar_curtailment_calculation(network.snapshots)

    if wind_present:
        m.add_variables(
          lower=0,
          dims=["snapshot"],
          coords={"snapshot": network.snapshots},
          name="Wind_curtailment"
      )
        def wind_curtailment_calculation(s):
            wind_generation = m.variables["Generator-p_nom"].loc["Wind"] * network.generators_t.p_max_pu["Wind"]
            # logger.debug(f"Wind generation p nom: {wind_generation}")

            wind_generation_1 = network.generators_t.p_max_pu["Wind"]
            # logger.debug(f"network generation:------------")
            # logger.debug(f"network generation: {wind_generation_1}")
            wind_allocation = m.variables["Generator-p"].loc[s, "Wind"]
            constraint_expr = m.variables['Wind_curtailment'] == (wind_generation - wind_allocation)
            m.add_constraints(constraint_expr, name="wind_curtailment_calculation_constraint")

        wind_curtailment_calculation(network.snapshots)

    m.add_variables(
        lower=0,
        dims=["snapshot"],
        coords={"snapshot": network.snapshots},
        name="Final_snapshot_curtailment"
    )

    m.objective += m.variables['Final_snapshot_curtailment'].sum()


    # Only enforce demand met during peak hours. Unmet demand is allowed outside peak hours.
    def add_peak_hour_constraint(peak_target=None, peak_hours=None):
        if peak_target is None or peak_hours is None:
            return  # skip if not provided

        # Mask for snapshots falling in user-defined peak hours
        peak_mask = network.snapshots.to_series().dt.hour.isin(peak_hours)

        total_peak_demand = network.loads_t.p_set.loc[peak_mask, "ElectricityDemand"].sum()
        peak_indices = network.snapshots[peak_mask]
        unmet_peak = m.variables["Generator-p"].loc[peak_indices, 'Unmet_Demand'].sum()

        # Ensure unmet demand <= (1 - peak_target) * demand during peak hours only
        constraint_expr = unmet_peak <= (1 - peak_target) * total_peak_demand
        m.add_constraints(constraint_expr, name="peak_hour_demand_constraint")

    # Only enforce the peak hour demand constraint. No annual demand offset constraint.
    add_peak_hour_constraint(peak_target=peak_target, peak_hours=peak_hours)

    # Step 4: Add State of Charge (SOC) and DoD constraint for storage
    if ess_name is not None:
        def add_SOC_DoD_constraint():
            snapshots_except_first = network.snapshots[1:].to_list()
                # The following strict DoD constraint is commented out to avoid infeasibility:
                # constraint_expr = m.variables["StorageUnit-state_of_charge"].loc[snapshots_except_first, 'Battery'] >= (1-DoD) * m.variables["StorageUnit-p_nom"]
                # m.add_constraints(constraint_expr, name="SOC_DoD_constraint")

        add_SOC_DoD_constraint()
        # Add battery energy capacity cap constraint (if provided)
        if Battery_max_energy_capacity is not None:
            # Human-readable: Battery_max_energy_capacity is in MWh, p_nom is MW, so max_hours = MWh/MW
            # PyPSA's max_hours is already set in setup_Components, but we can add a constraint for clarity
            max_energy = Battery_max_energy_capacity
            # For every snapshot, SOC <= p_nom * max_energy
            constraint_expr = m.variables["StorageUnit-state_of_charge"].loc[:, 'Battery'] <= m.variables["StorageUnit-p_nom"].loc['Battery'] * max_energy
            m.add_constraints(constraint_expr, name="battery_energy_capacity_cap_constraint")

    if solar_present and  wind_present:
        # Step 7: Final curtailment cost calculation
        def final_curtailment_cost_calculation(s):
            curtailment_marginal = (m.variables['Solar_curtailment'] * network.generators.at["Solar", "marginal_cost"]) + \
                                  (m.variables['Wind_curtailment'] * network.generators.at["Wind", "marginal_cost"])
            sell_curtailment = (sell_curtailment_percentage * (m.variables['Solar_curtailment'] + m.variables['Wind_curtailment'])) * curtailment_selling_price
            constraint_expr = m.variables['Final_snapshot_curtailment'] == (curtailment_marginal - sell_curtailment)
            m.add_constraints(constraint_expr, name="final_curtailment_cost_calculation_constraint")

        final_curtailment_cost_calculation(network.snapshots)

            # Step 8: Add annual curtailment upper limit constraint
        def add_annual_curtailment_upper_limit_constraint():
            annual_solar_curt = m.variables['Solar_curtailment'].sum()
            annual_wind_curt = m.variables['Wind_curtailment'].sum()
            annual_gen = (m.variables["Generator-p_nom"].loc["Solar"] * network.generators_t.p_max_pu["Solar"] +
                          m.variables["Generator-p_nom"].loc["Wind"] * network.generators_t.p_max_pu["Wind"]).sum()
            # logger.debug(f"Annual solar curtailment: {annual_solar_curt}")
            # logger.debug(f"Annual wind curtailment: {annual_wind_curt}")
            # logger.debug(f"Annual generation: {annual_gen}")

            annual_gen_1 = (network.generators_t.p_max_pu["Solar"] + network.generators_t.p_max_pu["Wind"]).sum()
            # logger.debug(f"Annual generation solar-----: {annual_gen_1}")

            annual_curt = annual_solar_curt + annual_wind_curt
            constraint_expr = annual_curt <= annual_curtailment_limit * annual_gen
            # logger.debug(f"Annual curtailment: {annual_curt}")
            # logger.debug(f"Annual curtailment limit: {annual_curtailment_limit * annual_gen}")
            m.add_constraints(constraint_expr, name="annual_curtailment_upper_limit_constraint")

        add_annual_curtailment_upper_limit_constraint()

    elif solar_present and not wind_present:
      def final_curtailment_cost_calculation(s):
            curtailment_marginal = (m.variables['Solar_curtailment'] * network.generators.at["Solar", "marginal_cost"])
            sell_curtailment = (sell_curtailment_percentage * (m.variables['Solar_curtailment'])) * curtailment_selling_price
            constraint_expr = m.variables['Final_snapshot_curtailment'] == (curtailment_marginal - sell_curtailment)
            m.add_constraints(constraint_expr, name="final_curtailment_cost_calculation_constraint")

      final_curtailment_cost_calculation(network.snapshots)

            # Step 8: Add annual curtailment upper limit constraint
      def add_annual_curtailment_upper_limit_constraint():
          annual_solar_curt = m.variables['Solar_curtailment'].sum()
          annual_gen = (m.variables["Generator-p_nom"].loc["Solar"] * network.generators_t.p_max_pu["Solar"]).sum()

        #   logger.debug(f"Annual solar curtailment: {annual_solar_curt}")
        #   logger.debug(f"Annual generation for only solar with Generator-p_nom : {annual_gen}")

          annual_gen_111 = (network.generators_t.p_max_pu["Solar"]).sum()
        #   logger.debug(f"Annual generation only solar : {annual_gen_111}")

          annual_curt = annual_solar_curt
          constraint_expr = annual_curt <= annual_curtailment_limit * annual_gen
          m.add_constraints(constraint_expr, name="annual_curtailment_upper_limit_constraint")

      add_annual_curtailment_upper_limit_constraint()

    elif wind_present and not solar_present:
      def final_curtailment_cost_calculation(s):
            curtailment_marginal = (m.variables['Wind_curtailment'] * network.generators.at["Wind", "marginal_cost"])
            sell_curtailment = (sell_curtailment_percentage * (m.variables['Wind_curtailment'])) * curtailment_selling_price
            constraint_expr = m.variables['Final_snapshot_curtailment'] == (curtailment_marginal - sell_curtailment)
            m.add_constraints(constraint_expr, name="final_curtailment_cost_calculation_constraint")

      final_curtailment_cost_calculation(network.snapshots)

            # Step 8: Add annual curtailment upper limit constraint
      def add_annual_curtailment_upper_limit_constraint():
          annual_wind_curt = m.variables['Wind_curtailment'].sum()
          annual_gen = (m.variables["Generator-p_nom"].loc["Wind"] * network.generators_t.p_max_pu["Wind"]).sum()
        #   logger.debug(f"Annual wind curtailment: {annual_wind_curt}")
        #   logger.debug(f"Annual generation for only wind with Generator-p_nom : {annual_gen}")

          annual_curt = annual_wind_curt
          constraint_expr = annual_curt <= annual_curtailment_limit * annual_gen
          m.add_constraints(constraint_expr, name="annual_curtailment_upper_limit_constraint")

      add_annual_curtailment_upper_limit_constraint()
    # logger.debug("Model optimization completed successfull {m.constraints}")
    # logger.debug("Model optimization completed successfull {m.objective}")
    # logger.debug("Model optimization completed successfull {m.variables}")

    # Add battery charging constraint (after all variables are defined)
    if ess_name is not None:
        battery_store = m.variables["StorageUnit-p_store"].loc[:, "Battery"]
        real_gen = None
        # Always define solar_gen for use in constraints below
        if solar_present:
            solar_gen = m.variables["Generator-p"].loc[:, "Solar"]
        else:
            # If solar is not present, set solar_gen to 0 (matching shape)
            solar_gen = 0
        if solar_present and wind_present:
            wind_gen = m.variables["Generator-p"].loc[:, "Wind"]
            real_gen = solar_gen + wind_gen
        elif solar_present:
            real_gen = solar_gen
        elif wind_present:
            real_gen = m.variables["Generator-p"].loc[:, "Wind"]
        if real_gen is not None:
            m.add_constraints(battery_store <= real_gen, name="battery_charge_from_real_gen_only")
            m.add_constraints(battery_store >= 0, name="battery_store_nonnegative")
            # Constraint: Battery discharge must serve peak hour demand as per user input
            if peak_hours is not None and len(peak_hours) > 0:
                peak_mask = network.snapshots.to_series().dt.hour.isin(peak_hours)
                peak_indices = network.snapshots[peak_mask]
                # Battery discharge during peak hours
                battery_discharge_peak = m.variables["StorageUnit-p_dispatch"].loc[peak_indices, "Battery"]
                # Total demand during peak hours
                total_peak_demand = network.loads_t.p_set.loc[peak_mask, "ElectricityDemand"].sum()
                # Enforce that battery discharge during peak hours meets user-defined fraction of peak demand
                # (e.g., peak_target = 0.9 means battery must serve at least 90% of peak demand)
                m.add_constraints(battery_discharge_peak.sum() >= peak_target * total_peak_demand, name="battery_discharge_meets_peak_demand")

                # --- Additional Constraints as per summary ---
                # 1. SOC update constraint (charging/discharging balance, efficiency, DoD)
                # (SOC_next = SOC_prev + charge * eff_store - discharge / eff_dispatch)
                # Enforce DoD: SOC >= (1-DoD) * p_nom
                if DoD is not None:
                    snapshots_except_first = network.snapshots[1:].to_list()
                    # Uncomment below for strict DoD enforcement
                    # m.add_constraints(m.variables["StorageUnit-state_of_charge"].loc[snapshots_except_first, 'Battery'] >= (1-DoD) * m.variables["StorageUnit-p_nom"], name="SOC_DoD_constraint")

                # 2. Solar → ESS first rule (then to demand, then curtailment)
                # Prioritize ESS charging from solar before demand/curtailment
                m.add_constraints(battery_store <= solar_gen, name="solar_to_ess_first")

                # 3. ESS discharge allowed only when solar < demand
                demand_series = network.loads_t.p_set["ElectricityDemand"]
                # Only allow battery discharge up to unmet demand after solar
                m.add_constraints(m.variables["StorageUnit-p_dispatch"].loc[:, "Battery"] <= demand_series - solar_gen, name="ess_discharge_only_when_solar_less_than_demand")

                # 4. PPA capacity limit (max deliverable = Solar_maxCapacity from user input)
                # Remove hardcoded 250 MW limit, use Solar_maxCapacity
                m.add_constraints(solar_gen <= Solar_maxCapacity, name="ppa_capacity_limit")

                # 5. Connectivity limit (cap solar export)
                # Remove hardcoded connectivity limit, use Solar_maxCapacity
                m.add_constraints(solar_gen <= Solar_maxCapacity, name="connectivity_limit")

                # 6. Curtailment calculation (any generation beyond PPA/Connectivity)
                # Curtailment = max(0, solar_gen - Solar_maxCapacity)
                curtailment = solar_gen - Solar_maxCapacity
                m.add_constraints(m.variables["Solar_curtailment"] >= curtailment, name="curtailment_calculation")
    return m
