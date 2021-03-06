import numpy as np
from src import dao as dao
from src import rate as rate
from src.storage import Storage
from src.preproc import preproc_peaktype, preproc_df, preproc_cost, preproc_demand_rate_m

from ortools.linear_solver import pywraplp

def build_solve_milp(st,
                    load_t, pv_t, cost_t,\
                     r_nbc, r_on_m, r_mid_m, r_off_m,\
                     c_whole, PCS_max, ef_c, ef_d, CAP_ess, SOC_min, SOC_max, CC):

    peaktype_list_by_month = st.get_idx_by_peak_type()
    onpeakidxlist = preproc_peaktype(peaktype_list_by_month, "ONPEAK")
    midpeakidxlist = preproc_peaktype(peaktype_list_by_month, "MIDPEAK")
    offpeakidxlist = preproc_peaktype(peaktype_list_by_month, "OFFPEAK")

    # load, pv, cost는 365 * 96 짜리 1열로 dissolved
    solver = pywraplp.Solver('MILP', pywraplp.Solver.CBC_MIXED_INTEGER_PROGRAMMING)
    # System var
    TIME_LENGTH = len(load_t)
    MONTH_LENGTH = 12

    # Decision var
    p_c_t = {}
    p_d_t = {}
    # u_t = {}
    for i in range(TIME_LENGTH):
        p_c_t[i] = solver.NumVar(0.0, PCS_max, 'p_c_' + str(i))
        p_d_t[i] = solver.NumVar(0.0, PCS_max, 'p_d_' + str(i))
        # u_t[i] = solver.IntVar(0.0, 1.0, 'u_' + str(i))

    # u_whole = solver.IntVar(0.0, 1.0, 'u_whole')

    # Dependent Var
    soc_t = {}
    pk_on_m = {}
    pk_mid_m = {}
    pk_off_m = {}
    for i in range(TIME_LENGTH):
        soc_t[i] = solver.NumVar(SOC_min, SOC_max, 'soc_' + str(i))

    for i in range(MONTH_LENGTH):
        pk_on_m[i] = solver.NumVar(0.0, solver.infinity(), 'pk_on_' + str(i))
        pk_mid_m[i] = solver.NumVar(0.0, solver.infinity(), 'pk_mid_' + str(i))
        pk_off_m[i] = solver.NumVar(0.0, solver.infinity(), 'pk_off_' + str(i))

    # Objective func
    solver.Minimize(
        # Energy Cost + Non-Bypassable-Cost + Wholesale
        # Energy Cost = sum(c_t * net_t)
        solver.Sum([cost_t[t] * (p_c_t[t] - p_d_t[t]) for t in range(TIME_LENGTH)])
        # Non-Bypassable-Cost = sum(r_nbc * abs(net_t))
        # + solver.Sum([r_nbc * 2 * u_t[t] * p_c_t[t] for t in range(TIME_LENGTH)])
        # + solver.Sum([r_nbc * -2 * u_t[t] * p_d_t[t] for t in range(TIME_LENGTH)])
        # + solver.Sum([-p_c_t[t] + p_d_t[t] for t in range(TIME_LENGTH)])
        # Wholesale
        # + solver.Sum([c_whole * u_whole *(p_c_t[t] - p_d_t[t]) for t in range(TIME_LENGTH)])
        # Demand Charge
        + solver.Sum([pk_on_m[m] * r_on_m[m] + pk_mid_m[m] * r_mid_m[m] + pk_off_m[m] * r_off_m[m]
                      for m in range(MONTH_LENGTH)]))
        # TODO : Customer Charge 미반영
        # Customer Charge
        # + CC)

    # Constraint rack
    const_peak_on = {}
    const_peak_mid = {}
    const_peak_off = {}
    const_soc = {}
    const_soc2 = {}
    # const_u_whole = {}
    # const_u = {}

    # Constraint1,2,3 : peak
    for m in np.unique(st.list_month):
        for idx, t in enumerate(onpeakidxlist):
            if st.list_month[int(idx/96)] == m:
                const_peak_on[idx] = solver.Add((load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t] - pk_on_m[m]) <= 0,
                                                'const_on_' + str(t))
        for idx, t in enumerate(midpeakidxlist):
            if st.list_month[int(idx/96)] == m:
                const_peak_mid[idx] = solver.Add((load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t] - pk_mid_m[m]) <= 0,
                                                'const_mid_' + str(t))
        for idx, t in enumerate(offpeakidxlist):
            if st.list_month[int(idx/96)] == m:
                const_peak_off[idx] = solver.Add((load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t] - pk_off_m[m]) <= 0,
                                                'const_off_' + str(t))

    # Constraint4 : SOC
    for t in range(1, TIME_LENGTH):
        const_soc[t - 1] = solver.Add(
            CAP_ess * soc_t[t] - CAP_ess * soc_t[t - 1] - 0.25*(ef_c * p_c_t[t] - ef_d * p_d_t[t]) == 0, 'test_' + str(t))
        # const_soc2[t - 1] = solver.Add(
        #     CAP_ess * soc_t[t] - CAP_ess * soc_t[t - 1] - 0.25*(ef_c * p_c_t[t] - ef_d * p_d_t[t]) >= 0, 'test2_' + str(t))
    const_soc2[0] = solver.Add(soc_t[0] == 0, 'init_soc')
    const_soc2[1] = solver.Add(p_c_t[0] == 0, 'init_pct')
    const_soc2[2] = solver.Add(p_d_t[0] == 0, 'init_pdt')
    # Constraint5 : u_whole
    # const_u_whole[0] = solver.Add(
    #     solver.Sum([u_t[t] * (load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t])
    #                 for t in range(TIME_LENGTH)]) * u_whole > 1, 'u_whole_higher')
    # const_u_whole[1] = solver.Add(
    #     u_whole < (1 / solver.Sum([u_t[t] * (load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t])
    #                                for t in range(TIME_LENGTH)]) + 1), 'u_whole_lower')

    # Constraint6 : u
    # for t in range(TIME_LENGTH):
    #     const_u[t] = solver.Add(u_t[t] * (load_t[t] - pv_t[t] + p_c_t[t] - p_d_t[t]) <= 0, 'cst_u_' + str(t))
    print("before")
    solver.Solve()

    dfsoc = st.df.copy()
    dfnet = st.df.copy()
    dfess = st.df.copy()

    rowlist = dfsoc.index.tolist()
    collist = dfsoc.columns.tolist()

    for i in range(len(load_t)):
        tmprow, tmpcol = divmod(i, 96)
        row = rowlist[tmprow]
        col = collist[tmpcol]
        dfsoc.loc[row, col] = soc_t[i].solution_value()
        dfess.loc[row, col] = p_c_t[i].solution_value() - p_d_t[i].solution_value()
        dfnet.loc[row, col] = load_t[i]
        dfnet.loc[row, col] += dfess.loc[row, col]
        dfnet.loc[row, col] -= pv_t[i]

    return dfnet, dfsoc, dfess


def getScheduleStorage(load, pv, RATEOPTION):
    st = Storage(df=load, RATE=RATEOPTION)
    load_t = preproc_df(load)
    pv_t = preproc_df(pv)

    r_nbc = st.RATE["NBC"]
    r_on_m = preproc_demand_rate_m(st, "ONPEAK")
    r_mid_m = preproc_demand_rate_m(st, "MIDPEAK")
    r_off_m = preproc_demand_rate_m(st, "OFFPEAK")

    c_whole = 0.05
    PCS_max = 500.0
    CAP_ESS = 1000.0
    SOC_min = 0.0
    SOC_max = 1.0
    CC = st.RATE["CC"]

    ef_c = 0.96
    ef_d = (1.0 / 0.96)

    solver = build_solve_milp(st,
                              load_t=load_t, pv_t=pv_t, \
                              cost_t=preproc_cost(st), \
                              r_nbc=r_nbc, r_on_m=r_on_m, r_mid_m=r_mid_m, r_off_m=r_off_m, \
                              c_whole=c_whole, PCS_max=PCS_max, \
                              ef_c=ef_c, ef_d=ef_d, CAP_ess=CAP_ESS, SOC_min=SOC_min, SOC_max=SOC_max, CC=CC)


def test():
    load = dao.getRawLoad()
    pv = dao.getRawPV()
    # es = dao.getRawES()
    # net = dao.getRawNet()

    # for test
    # load = load.loc[load.index[[i for i in range(0, 31)]]]
    # pv = pv.loc[pv.index[[i for i in range(0, 31)]]]
    # es = dao.getRawES().drop(es.index[[i for i in range(31, 365)]])
    # net = dao.getRawNet().drop(net.index[[i for i in range(31, 365)]])

    st = Storage(df=load, RATE=rate.TOU8_OPTION_R)
    # cost_t = preproc_cost(st)
    load_t = preproc_df(load)
    pv_t = preproc_df(pv)

    r_nbc = st.RATE["NBC"]

    r_on_m = preproc_demand_rate_m(st, "ONPEAK")
    r_mid_m = preproc_demand_rate_m(st, "MIDPEAK")
    r_off_m = preproc_demand_rate_m(st, "OFFPEAK")

    c_whole = 0.05
    PCS_max = 500.0
    CAP_ESS = 1000.0
    SOC_min = 0.0
    SOC_max = 1.0
    CC = st.RATE["CC"]

    ef_c = 0.96
    ef_d = (1.0 / 0.96)
    #Î
    dfnet, dfsoc, dfess = build_solve_milp(st,
                     load_t=load_t, pv_t=pv_t,\
                     cost_t=preproc_cost(st),\
                     r_nbc=r_nbc, r_on_m=r_on_m, r_mid_m=r_mid_m, r_off_m=r_off_m,\
                     c_whole=c_whole, PCS_max=PCS_max,\
                     ef_c=ef_c, ef_d=ef_d, CAP_ess=CAP_ESS, SOC_min=SOC_min, SOC_max=SOC_max, CC=CC)

    print("hi")

if __name__ == '__main__':
    test()
    print("hi")
