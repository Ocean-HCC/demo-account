-- 2026-09-29 按意图去掉 ETF（意图 3 交付范围；实现 4.4 迁移）：
-- 不再区分资产类型，也不再有 ETF 复权因子。先清掉已缓存的 ETF 标的与 ETF 因子记录，再删列。
DELETE FROM instruments WHERE asset_type <> 'stock';
DELETE FROM corporate_actions WHERE factor IS NOT NULL;
ALTER TABLE instruments DROP COLUMN asset_type;
ALTER TABLE corporate_actions DROP COLUMN factor;
