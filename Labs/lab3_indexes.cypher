// Начальная точка обхода лабы 3: MATCH (g:Group {id: $group_id}).
// Выполнить один раз при настройке БД, не внутри HTTP-обработчика.
CREATE INDEX lab3_group_id IF NOT EXISTS FOR (g:Group) ON (g.id);
