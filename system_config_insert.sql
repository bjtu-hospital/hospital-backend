-- ===================================================================
-- 系统配置初始化 SQL
-- 用于插入 registration (挂号配置) 和 schedule (排班配置)
-- ===================================================================

-- 插入挂号配置 (registration)
INSERT INTO `system_config` 
    (`config_key`, `scope_type`, `scope_id`, `config_value`, `data_type`, `description`, `is_active`, `create_time`, `update_time`)
VALUES 
    (
        'registration',
        'GLOBAL',
        NULL,
        JSON_OBJECT(
            'advanceBookingDays', 14,
            'sameDayDeadline', '08:00',
            'noShowLimit', 3,
            'cancelHoursBefore', 24,
            'sameClinicInterval', 7
        ),
        'JSON',
        '挂号配置：包含提前挂号天数、当日挂号截止时间、爽约次数限制、退号提前时间、同科室挂号间隔',
        1,
        NOW(),
        NOW()
    );

-- 插入排班配置 (schedule)
INSERT INTO `system_config` 
    (`config_key`, `scope_type`, `scope_id`, `config_value`, `data_type`, `description`, `is_active`, `create_time`, `update_time`)
VALUES 
    (
        'schedule',
        'GLOBAL',
        NULL,
        JSON_OBJECT(
            'maxFutureDays', 60,
            'morningStart', '08:00',
            'morningEnd', '12:00',
            'afternoonStart', '14:00',
            'afternoonEnd', '18:00',
            'eveningStart', '18:30',
            'eveningEnd', '21:00',
            'consultationDuration', 15,
            'intervalTime', 5
        ),
        'JSON',
        '排班配置：包含最多排未来天数、上午/下午/晚班时间段、单次就诊时长、就诊间隔时间',
        1,
        NOW(),
        NOW()
    );

-- 查询验证插入结果
SELECT 
    config_id,
    config_key,
    scope_type,
    JSON_PRETTY(config_value) AS config_value,
    description,
    is_active,
    create_time,
    update_time
FROM 
    system_config
WHERE 
    config_key IN ('registration', 'schedule')
ORDER BY 
    config_key;
