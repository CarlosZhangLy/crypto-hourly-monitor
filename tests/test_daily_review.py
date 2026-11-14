"""Offline invariants for the paper-trading simulator."""
import unittest
from datetime import datetime,timezone,timedelta
from daily_review import (FEE,LEVELS,protect_stop,gross_net,process_position,
                          fill_trade,limit_fill,initialize,tabulate,replay,ms)
BJ=timezone(timedelta(hours=8))
class BacktestTests(unittest.TestCase):
    def example(self,direction="多"):
        sent=datetime(2026,11,14,9,34,tzinfo=BJ)
        p={"id":"test001","symbol":"LTC","direction":direction,"entry":"100",
           "stop":"98" if direction=="多" else "102","contracts":"0.3",
           "ctval":"1","leverage":3,"score":70,"retest_band":"0.1","tick_sz":"0.01"}
        return initialize((sent,p),"A")
    def bar(self,o=100,h=100,l=100,c=100):
        return {"ts":datetime(2026,11,14,9,35,tzinfo=BJ).timestamp()*1000,
                "open":o,"high":h,"low":l,"close":c,"volume":100}
    def test_fee_guard_makes_net_positive(self):
        for side in ("多","空"):
            t=self.example(side)
            fill_trade(t,100,self.bar(),{"A|test001":t})
            stop=protect_stop(100,side,0.0012,0.01)
            gross,fees,net=gross_net(t,stop)
            self.assertGreater(net,0)
            self.assertGreaterEqual(net,0.3*(0.0019))
    def test_maker_taker_cost_bounds(self):
        self.assertEqual(FEE,0.0005)
        self.assertEqual(LEVELS[0][:2],(0.004,0.0012))
    def test_before_40_percent_original_stop(self):
        t=self.example("多")
        fill_trade(t,100,self.bar(),{"A|test001":t})
        process_position(t,self.bar(o=100.1,h=100.39,l=99.3,c=99.4))
        self.assertEqual(t["status"],"OPEN")
        self.assertEqual(t["protect_stop"],98)
        process_position(t,self.bar(o=99.4,h=99.5,l=97.98,c=98))
        self.assertEqual(t["status"],"CLOSED")
        self.assertEqual(t["exit_reason"],"原始止损")
        self.assertLess(t["net_pnl"],0)
    def test_first_milestone_moves_stop(self):
        t=self.example("多")
        fill_trade(t,100,self.bar(),{"A|test001":t})
        process_position(t,self.bar(o=100.1,h=100.41,l=100.3,c=100.4))
        self.assertEqual(t["highest_level"],1)
        self.assertGreaterEqual(t["protect_stop"],100.12-1e-8)
        process_position(t,self.bar(o=100.2,h=100.22,l=100.1,c=100.12))
        self.assertEqual(t["status"],"CLOSED")
        self.assertGreater(t["net_pnl"],0)
    def test_all_milestones(self):
        t=self.example("空")
        fill_trade(t,100,self.bar(),{"A|test001":t})
        process_position(t,self.bar(o=99.9,h=99.9,l=91.9,c=92))
        self.assertEqual(t["status"],"CLOSED")
        self.assertEqual(t["exit_reason"],"800%档位止盈")
        self.assertGreater(t["net_pnl"],0)
    def test_limit_marketable_immediately(self):
        t=self.example("多")
        self.assertEqual(limit_fill(t,self.bar(o=99.6,h=100,l=99.5,c=100)),99.6)
        t=self.example("空")
        self.assertEqual(limit_fill(t,self.bar(o=100.5,h=101,l=100,c=100.1)),100.5)
    def test_portfolio_max_two_positions(self):
        t1=self.example()
        t2=self.example()
        t2["id"]="different"
        t2["symbol"]="XRP"
        t3=self.example()
        t3["id"]="third"
        t3["symbol"]="SOL"
        d={"A|1":t1,"A|2":t2,"A|3":t3}
        fill_trade(t1,100,self.bar(),d)
        fill_trade(t2,100,self.bar(),d)
        fill_trade(t3,100,self.bar(),d)
        self.assertEqual(t3["status"],"SKIPPED")

    def test_full_daily_replay_with_synthetic_minute_candles(self):
        sent=datetime(2026,11,14,9,34,tzinfo=BJ)
        p={"id":"replay01","symbol":"LTC","direction":"多","entry":"100",
           "stop":"98","contracts":"0.3","ctval":"1","leverage":3,
           "score":71,"tick_sz":"0.01","retest_band":"0.1"}
        records={"replay01":(sent,p)}
        def feed(inst,start,end):
            self.assertEqual(inst,"LTC-USDT-SWAP")
            result=[]
            first=ms(sent)+60000
            for ts in range(start,end+1,60000):
                o,h,l,c=100,100.1,99.9,100
                if ts==first:o,h,l,c=100,100.5,100.3,100.4
                if ts==first+60000:o,h,l,c=100.3,100.3,100.1,100.2
                result.append({"ts":ts,"open":o,"high":h,"low":l,"close":c,"volume":100})
            return result
        trades={}
        cutoff=ms(sent+timedelta(hours=2,minutes=10))
        failures=replay(trades,records,cutoff,fetcher=feed)
        self.assertFalse(failures)
        self.assertEqual(trades["A|replay01"]["status"],"CLOSED")
        self.assertGreater(trades["A|replay01"]["net_pnl"],0)
        self.assertEqual(trades["B|replay01"]["status"],"EXPIRED")
        report,summary=tabulate(trades,records,"2026-11-14",sent+timedelta(hours=3),{})
        self.assertIn("限价/成交价",report)
        self.assertEqual(summary["A"]["filled"],1)
        self.assertEqual(summary["B"]["expired"],1)

if __name__=="__main__":unittest.main()
