# -*- coding:utf-8 -*-
# Filename: viterbi.py
# Author：hankcs
# Date: 2014-05-13 下午8:51
# blogs:http://www.hankcs.com/nlp/hmm-and-segmentation-tagging-named-entity-recognition.html

states = ('Rainy', 'Sunny')

observations = ('walk', 'shop', 'clean')

start_probability = {'Rainy': 0.6, 'Sunny': 0.4}

transition_probability = {
    'Rainy': {'Rainy': 0.7, 'Sunny': 0.3},
    'Sunny': {'Rainy': 0.4, 'Sunny': 0.6},
}

emission_probability = {
    'Rainy': {'walk': 0.1, 'shop': 0.4, 'clean': 0.5},
    'Sunny': {'walk': 0.6, 'shop': 0.3, 'clean': 0.1},
}


# 打印路径概率表
def print_dptable(V):
    print("    ")
    for i in range(len(V)):
        print("%7d" % i,end='')

    print("")
    for state in V[0].keys():
        print("%s: " % state, end='')
        for t in range(len(V)):
            print("%s " % ("%.5f" % V[t][state]), end='')
        print("")

def viterbi(obs, states, start_p, trans_p, emit_p):
    """

    :param obs:观测序列
    :param states:隐状态
    :param start_p:初始概率（隐状态）
    :param trans_p:转移概率（隐状态）
    :param emit_p: 发射概率 （隐状态表现为显状态的概率）
    :return:
    """
    # 路径概率表 V[时间][隐状态] = 概率
    # V [{'Rainy': 0.06, 'Sunny': 0.24}, {'Rainy': 0.0384, 'Sunny': 0.043}, {'Rainy': 0.01344, 'Sunny': 0.002}]
    V = [{}]
    # 一个中间变量，代表当前状态是哪个隐状态
    path = {} # key代表最新的隐状态

    # 初始化初始状态 (t == 0)
    for cur_state in states:
        V[0][cur_state] = start_p[cur_state] * emit_p[cur_state][obs[0]]
        path[cur_state] = [cur_state]

    # 对 t > 0 跑一遍维特比算法
    for t in range(1, len(obs)):
        V.append({})
        newpath = {}

        for cur_state in states:
            # 概率 隐状态 =    前状态是pre_state的概率 * pre_state转移到state的概率 * state发射为当前状态的概率
            (prob, state) = max([(V[t - 1][pre_state] * trans_p[pre_state][cur_state] * emit_p[cur_state][obs[t]], pre_state) \
                                 for pre_state in states])
            # 记录最大概率
            V[t][cur_state] = prob
            # 记录路径
            newpath[cur_state] = path[state] + [cur_state] # 相当于append(cur_state)

        # 不需要保留旧路径
        path = newpath

    print_dptable(V)
    (prob, state) = max([(V[len(obs) - 1][state], state) for state in states])
    print("V:",V) # V [{'Rainy': 0.06, 'Sunny': 0.24}, {'Rainy': 0.0384, 'Sunny': 0.043}, {'Rainy': 0.01344, 'Sunny': 0.002}]
    print("path:",path) # V [{'Rainy': 0.06, 'Sunny': 0.24}, {'Rainy': 0.0384, 'Sunny': 0.043}, {'Rainy': 0.01344, 'Sunny': 0.002}]
    return (prob, path[state])

def example():
    return viterbi(observations,
                   states,
                   start_probability,
                   transition_probability,
                   emission_probability)


print("final result:",example())
