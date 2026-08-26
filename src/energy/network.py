"""Build a PyPSA model of the existing electricity system."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pypsa


def _require_columns(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = columns - set(frame.columns)
    if missing:
        raise ValueError(f"{label} missing columns: {sorted(missing)}")


def _time_step_hours(index: pd.Index) -> float:
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError("Demand profile index must contain dates and times")
    if index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Demand profile dates and times must be unique and ordered")
    if len(index) < 2:
        return 1.0

    intervals = index.to_series().diff().dropna().dt.total_seconds() / 3600
    if (intervals <= 0).any() or not np.allclose(intervals, intervals.iloc[0]):
        raise ValueError("Demand profile must use one regular time-step length")
    return float(intervals.iloc[0])


def _bus_voltage_kv(
    bus_id: str,
    bus_row: pd.Series,
    connected_voltages: set[float],
) -> float:
    """Return one nominal bus voltage consistent with its connected AC lines."""
    connected_voltage_values = np.array(sorted(connected_voltages), dtype=float)

    explicit_voltage = bus_row.get("v_nom_kv")
    if pd.notna(explicit_voltage):
        explicit_voltage = float(explicit_voltage)
        if connected_voltage_values.size and not np.allclose(
            connected_voltage_values,
            explicit_voltage,
        ):
            raise ValueError(f"Bus {bus_id} voltage does not match its connected line voltages")
        return explicit_voltage
    if connected_voltage_values.size == 1:
        return float(connected_voltage_values[0])
    if connected_voltage_values.size > 1:
        raise ValueError(
            f"Bus {bus_id} has multiple line voltages. Represent each voltage "
            "level as a separate bus connected by a Transformer."
        )
    return 66.0


def build_topology_network(
    buses: gpd.GeoDataFrame,
    lines: gpd.GeoDataFrame,
    generators: pd.DataFrame,
    *,
    line_resistance_ohm_per_km: float = 0.01,
    line_reactance_ohm_per_km: float = 0.4,
) -> pypsa.Network:
    """Build a fixed-capacity network topology without demand time series.

    The model cannot build extra capacity. Missing line limits or power-station
    capacities cause a clear error rather than being estimated by the model.

    Generator ``output_capacity_mw`` is electrical output capacity and is
    passed directly to ``Generator.p_nom``. Line ``s_nom_mva`` is an
    apparent-power rating. This function does not convert capacities to or
    from an LHV basis.
    """
    if "output_capacity_mw" not in generators and "capacity_mw" in generators:
        raise ValueError(
            "generators.capacity_mw has been renamed to output_capacity_mw; "
            "rename the column before building the network"
        )
    _require_columns(buses, {"bus_id", "geometry"}, "buses")
    _require_columns(
        lines,
        {
            "line_id",
            "bus0",
            "bus1",
            "v_nom_kv",
            "length_km",
            "s_nom_mva",
        },
        "lines",
    )
    _require_columns(
        generators,
        {
            "generator_id",
            "bus_id",
            "carrier",
            "output_capacity_mw",
            "marginal_cost",
        },
        "generators",
    )

    if lines["s_nom_mva"].isna().any():
        raise ValueError("Maximum line power is incomplete; populate s_nom_mva before simulation")
    if generators["output_capacity_mw"].isna().any():
        raise ValueError("Power-station maximum output is incomplete; populate output_capacity_mw before simulation")
    if "capacity_basis" in generators:
        invalid_basis = ~generators["capacity_basis"].astype(str).str.lower().eq("electrical_output")
        if invalid_basis.any():
            raise ValueError("Generator capacity_basis must be 'electrical_output'; Generator.p_nom is output-side MW")
    if generators["marginal_cost"].isna().any():
        raise ValueError("Power-station running costs are incomplete; populate marginal_cost before simulation")
    if generators["bus_id"].isna().any():
        raise ValueError("Power-station substation assignments are incomplete")

    network = pypsa.Network()
    network.add("Carrier", "AC")

    connected_voltages: dict[str, set[float]] = {}
    for row in lines[["bus0", "bus1", "v_nom_kv"]].itertuples(index=False):
        if pd.isna(row.v_nom_kv):
            continue
        voltage = float(row.v_nom_kv)
        connected_voltages.setdefault(str(row.bus0), set()).add(voltage)
        connected_voltages.setdefault(str(row.bus1), set()).add(voltage)

    bus_frame = buses.to_crs("EPSG:4326").copy()
    bus_frame["bus_id"] = bus_frame["bus_id"].astype(str)
    bus_frame = bus_frame.set_index("bus_id")
    bus_voltages = [
        _bus_voltage_kv(
            bus_id,
            row,
            connected_voltages.get(bus_id, set()),
        )
        for bus_id, row in bus_frame.iterrows()
    ]
    network.madd(
        "Bus",
        bus_frame.index,
        x=bus_frame.geometry.x.to_numpy(),
        y=bus_frame.geometry.y.to_numpy(),
        v_nom=bus_voltages,
        carrier="AC",
    )
    for column in (
        "name",
        "kind",
        "asset_id",
        "is_root",
        "inferred",
        "source",
        "region",
        "provisional_root",
        "anchor_status",
        "anchor_distance_m",
    ):
        if column in bus_frame:
            network.buses[column] = bus_frame[column].reindex(network.buses.index)

    line_frame = lines.copy()
    line_frame["line_id"] = line_frame["line_id"].astype(str)
    line_frame = line_frame.set_index("line_id")
    lengths = line_frame["length_km"].astype(float).to_numpy()
    # PyPSA Line.s_nom is the branch apparent-power rating in MVA.
    network.madd(
        "Line",
        line_frame.index,
        bus0=line_frame["bus0"].astype(str).to_numpy(),
        bus1=line_frame["bus1"].astype(str).to_numpy(),
        carrier="AC",
        length=lengths,
        s_nom=line_frame["s_nom_mva"].astype(float).to_numpy(),
        s_nom_extendable=False,
        r=np.maximum(lengths * line_resistance_ohm_per_km, 1e-6),
        x=np.maximum(lengths * line_reactance_ohm_per_km, 1e-6),
    )

    if "geometry" in lines.columns:
        line_geometry = line_frame["geometry"]
        valid_geometry = line_geometry.map(
            lambda geometry: geometry.wkt if geometry is not None and not geometry.is_empty else ""
        )
        network.lines["geometry"] = valid_geometry.reindex(network.lines.index).fillna("")
    for column in (
        "inferred",
        "source",
        "region",
        "stage",
        "source_route_id",
        "source_route_part_id",
        "circuit_id",
        "derived",
        "rating_basis",
    ):
        if column in line_frame:
            network.lines[column] = line_frame[column].reindex(network.lines.index)

    for _, row in generators.iterrows():
        carrier = str(row["carrier"])
        if carrier not in network.carriers.index:
            network.add("Carrier", carrier)
        efficiency = row.get("efficiency", 1.0)
        if pd.isna(efficiency):
            efficiency = 1.0
        # Generator.p_nom limits electrical output at the connected bus.
        network.add(
            "Generator",
            str(row["generator_id"]),
            bus=str(row["bus_id"]),
            carrier=carrier,
            p_nom=float(row["output_capacity_mw"]),
            p_nom_extendable=False,
            marginal_cost=float(row["marginal_cost"]),
            efficiency=float(efficiency),
        )

    assert_fixed_capacity(network)
    return network


def attach_demand(
    network: pypsa.Network,
    demand_profile: pd.DataFrame,
    service_weights: pd.DataFrame,
    *,
    generator_availability: pd.DataFrame | None = None,
    value_of_lost_load: float = 10_000,
) -> pypsa.Network:
    """Return a run-ready copy of ``network`` with demand and load shedding."""
    _require_columns(service_weights, {"bus_id", "service_weight"}, "service_weights")

    if not network.loads.empty or (
        "carrier" in network.generators and network.generators.carrier.eq("load_shedding").any()
    ):
        raise ValueError("Demand is already attached to this network")
    if demand_profile.empty:
        raise ValueError("Demand profile is empty")
    demand_profile = demand_profile.apply(pd.to_numeric, errors="coerce")
    if demand_profile.isna().any().any():
        raise ValueError("Demand profile contains missing values")
    if (demand_profile < 0).any().any():
        raise ValueError("Demand profile cannot contain negative demand")
    if generator_availability is not None:
        if not generator_availability.index.equals(demand_profile.index):
            raise ValueError("Generator availability must use the same timestamps as demand_profile")
        generator_availability = generator_availability.apply(
            pd.to_numeric,
            errors="coerce",
        )
        if generator_availability.isna().any().any():
            raise ValueError("Generator availability contains missing values")
        if (generator_availability < 0).any().any() or (generator_availability > 1).any().any():
            raise ValueError("Generator availability values must lie between zero and one")

    run_network = network.copy()
    time_step_hours = _time_step_hours(demand_profile.index)
    run_network.set_snapshots(demand_profile.index)
    run_network.snapshot_weightings.loc[:, :] = time_step_hours

    physical_generator_ids = (
        run_network.generators.index[~run_network.generators.carrier.eq("load_shedding")].astype(str).tolist()
    )
    if generator_availability is not None:
        availability = generator_availability.reindex(columns=physical_generator_ids)
        if availability.isna().any().any():
            missing = sorted(set(physical_generator_ids) - set(generator_availability.columns))
            raise ValueError(
                f"Generator availability must contain one complete column per generator_id; missing: {missing}"
            )
        run_network.generators_t.p_max_pu.loc[:, physical_generator_ids] = availability

    bus_ids = run_network.buses.index.astype(str)
    if service_weights["bus_id"].astype(str).duplicated().any():
        raise ValueError("Demand shares contain duplicate bus_id values")
    supplied_bus_ids = set(service_weights["bus_id"].astype(str))
    unknown_bus_ids = sorted(supplied_bus_ids - set(bus_ids))
    if unknown_bus_ids:
        raise ValueError(f"Demand shares reference unknown buses: {unknown_bus_ids}")
    weights = (
        service_weights.assign(bus_id=service_weights["bus_id"].astype(str))
        .set_index("bus_id")["service_weight"]
        .reindex(bus_ids, fill_value=0.0)
    )
    if weights.isna().any() or not np.isclose(weights.sum(), 1.0):
        raise ValueError("Demand shares must contain valid bus_id values and add to one")

    if "demand_mw" in demand_profile.columns:
        total_demand = demand_profile["demand_mw"]
        demand_by_bus = pd.DataFrame({bus_id: total_demand * weight for bus_id, weight in weights.items()})
    else:
        demand_by_bus = demand_profile.reindex(columns=bus_ids)
        if demand_by_bus.isna().any().any():
            raise ValueError("Demand profile must contain demand_mw or one complete column per substation (bus_id)")

    demand_bus_ids = demand_by_bus.columns[demand_by_bus.abs().max(axis=0).gt(0)].astype(str)
    if "load_shedding" not in run_network.carriers.index:
        run_network.add("Carrier", "load_shedding")
    for bus_id in demand_bus_ids:
        load_id = f"load::{bus_id}"
        shed_id = f"load_shedding::{bus_id}"
        run_network.add("Load", load_id, bus=bus_id)
        run_network.loads_t.p_set[load_id] = demand_by_bus[bus_id]
        run_network.add(
            "Generator",
            shed_id,
            bus=bus_id,
            carrier="load_shedding",
            p_nom=max(float(demand_by_bus[bus_id].max()), 1.0),
            p_nom_extendable=False,
            marginal_cost=float(value_of_lost_load),
        )

    assert_fixed_capacity(run_network)
    return run_network


def build_operational_network(
    buses: gpd.GeoDataFrame,
    lines: gpd.GeoDataFrame,
    generators: pd.DataFrame,
    demand_profile: pd.DataFrame,
    service_weights: pd.DataFrame,
    *,
    generator_availability: pd.DataFrame | None = None,
    value_of_lost_load: float = 10_000,
    line_resistance_ohm_per_km: float = 0.01,
    line_reactance_ohm_per_km: float = 0.4,
) -> pypsa.Network:
    """Build a time-series supply model using only existing assets."""
    topology = build_topology_network(
        buses,
        lines,
        generators,
        line_resistance_ohm_per_km=line_resistance_ohm_per_km,
        line_reactance_ohm_per_km=line_reactance_ohm_per_km,
    )
    return attach_demand(
        topology,
        demand_profile,
        service_weights,
        generator_availability=generator_availability,
        value_of_lost_load=value_of_lost_load,
    )


def assert_fixed_capacity(network: pypsa.Network) -> None:
    """Reject settings that let the model build extra assets."""
    checks = (
        ("generators", "p_nom_extendable"),
        ("lines", "s_nom_extendable"),
        ("links", "p_nom_extendable"),
        ("storage_units", "p_nom_extendable"),
        ("stores", "e_nom_extendable"),
    )
    for component, column in checks:
        frame = getattr(network, component)
        if column in frame and frame[column].fillna(False).any():
            raise ValueError(f"{component}.{column} must be false for interruption modelling")
