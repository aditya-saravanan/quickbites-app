-- Fixture DB for unit tests. Mirrors app.db schema (subset).
-- Designed to exercise every abuse signal in isolation + combined.

CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT NOT NULL,
    city TEXT NOT NULL, joined_at TEXT NOT NULL, loyalty_tier TEXT NOT NULL,
    wallet_balance_inr INTEGER NOT NULL
);
CREATE TABLE restaurants (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, cuisine TEXT NOT NULL,
    city TEXT NOT NULL, area TEXT NOT NULL, joined_at TEXT NOT NULL
);
CREATE TABLE riders (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL,
    city TEXT NOT NULL, joined_at TEXT NOT NULL
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL,
    restaurant_id INTEGER NOT NULL, rider_id INTEGER,
    placed_at TEXT NOT NULL, delivered_at TEXT, status TEXT NOT NULL,
    subtotal_inr INTEGER NOT NULL, delivery_fee_inr INTEGER NOT NULL,
    total_inr INTEGER NOT NULL, payment_method TEXT NOT NULL,
    promo_code TEXT, address TEXT NOT NULL
);
CREATE TABLE order_items (
    id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL,
    item_name TEXT NOT NULL, qty INTEGER NOT NULL, price_inr INTEGER NOT NULL
);
CREATE TABLE complaints (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
    target_type TEXT NOT NULL, target_id INTEGER, raised_at TEXT NOT NULL,
    description TEXT NOT NULL, status TEXT NOT NULL, resolution TEXT NOT NULL,
    resolution_amount_inr INTEGER NOT NULL
);
CREATE TABLE refunds (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
    amount_inr INTEGER NOT NULL, type TEXT NOT NULL, issued_at TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE reviews (
    id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
    restaurant_id INTEGER NOT NULL, rating INTEGER NOT NULL,
    comment TEXT, created_at TEXT NOT NULL
);
CREATE TABLE rider_incidents (
    id INTEGER PRIMARY KEY, rider_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
    type TEXT NOT NULL, reported_at TEXT NOT NULL, verified INTEGER NOT NULL,
    notes TEXT
);

-- Seed
INSERT INTO restaurants VALUES (1, 'Tandoor House', 'north_indian', 'Mumbai', 'Bandra', '2024-01-01');
INSERT INTO riders VALUES (1, 'Ravi K', '+91-9000000001', 'Mumbai', '2024-06-01');

-- Customer 1: clean_loyal — 10 orders, 0 complaints, gold tier, established
INSERT INTO customers VALUES (1, 'Asha Patel', '+91-9000000010', 'asha@example.com',
    'Mumbai', '2024-01-15', 'gold', 500);

-- Customer 2: mild_complainer — 8 orders, 2 complaints (25% rate, doesn't fire 0.50)
INSERT INTO customers VALUES (2, 'Bina Shah', '+91-9000000011', 'bina@example.com',
    'Mumbai', '2024-03-01', 'silver', 0);

-- Customer 3: refund_burster — 6 orders, 4 refunds in last 30 days
INSERT INTO customers VALUES (3, 'Chirag Mehta', '+91-9000000012', 'chirag@example.com',
    'Mumbai', '2024-02-01', 'silver', 100);

-- Customer 4: new_account_abuser — joined <30d ago, 3 complaints
INSERT INTO customers VALUES (4, 'Devi Rao', '+91-9000000013', 'devi@example.com',
    'Mumbai', '2026-04-01', 'bronze', 0);

-- Customer 5: rejection_repeater — 5 rejected complaints
INSERT INTO customers VALUES (5, 'Eshan K', '+91-9000000014', 'eshan@example.com',
    'Mumbai', '2024-01-01', 'bronze', 0);

-- Customer 6: combined_abuser — high complaint rate + refund burst + rejections
INSERT INTO customers VALUES (6, 'Faisal R', '+91-9000000015', 'faisal@example.com',
    'Mumbai', '2024-01-01', 'bronze', 0);

-- Customer 1: 10 delivered orders, 0 complaints
INSERT INTO orders VALUES (101, 1, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered',
    400, 30, 430, 'upi', NULL, '12 MG Road, Mumbai, 9000-0001');
INSERT INTO order_items VALUES (1001, 101, 'Butter Chicken', 1, 350);
INSERT INTO order_items VALUES (1002, 101, 'Naan', 2, 25);
INSERT INTO orders VALUES (102, 1, 1, 1, '2026-04-08', '2026-04-08T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (103, 1, 1, 1, '2026-04-06', '2026-04-06T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (104, 1, 1, 1, '2026-04-04', '2026-04-04T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (105, 1, 1, 1, '2026-04-02', '2026-04-02T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (106, 1, 1, 1, '2026-03-30', '2026-03-30T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (107, 1, 1, 1, '2026-03-25', '2026-03-25T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (108, 1, 1, 1, '2026-03-20', '2026-03-20T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (109, 1, 1, 1, '2026-03-15', '2026-03-15T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (110, 1, 1, 1, '2026-03-10', '2026-03-10T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');

-- Customer 2: 8 orders, 2 complaints (25% rate)
INSERT INTO orders VALUES (201, 2, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (202, 2, 1, 1, '2026-04-08', '2026-04-08T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (203, 2, 1, 1, '2026-04-06', '2026-04-06T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (204, 2, 1, 1, '2026-04-04', '2026-04-04T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (205, 2, 1, 1, '2026-04-02', '2026-04-02T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (206, 2, 1, 1, '2026-03-30', '2026-03-30T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (207, 2, 1, 1, '2026-03-25', '2026-03-25T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (208, 2, 1, 1, '2026-03-20', '2026-03-20T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO complaints VALUES (2001, 2, 201, 'restaurant', 1, '2026-04-10', 'cold', 'resolved', 'apology', 0);
INSERT INTO complaints VALUES (2002, 2, 202, 'restaurant', 1, '2026-04-08', 'late', 'resolved', 'credit', 50);

-- Customer 3: 6 orders, 4 refunds in last 30d
INSERT INTO orders VALUES (301, 3, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (302, 3, 1, 1, '2026-04-05', '2026-04-05T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (303, 3, 1, 1, '2026-03-30', '2026-03-30T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (304, 3, 1, 1, '2026-03-25', '2026-03-25T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (305, 3, 1, 1, '2026-03-20', '2026-03-20T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (306, 3, 1, 1, '2026-03-15', '2026-03-15T13:00', 'delivered', 500, 30, 530, 'upi', NULL, 'addr');
INSERT INTO refunds VALUES (3001, 3, 301, 200, 'wallet_credit', '2026-04-10', 'cold');
INSERT INTO refunds VALUES (3002, 3, 302, 250, 'wallet_credit', '2026-04-05', 'cold');
INSERT INTO refunds VALUES (3003, 3, 303, 200, 'wallet_credit', '2026-03-30', 'cold');
INSERT INTO refunds VALUES (3004, 3, 304, 200, 'wallet_credit', '2026-03-25', 'cold');

-- Customer 4: new account, 3 complaints (joined 2026-04-01, snapshot 2026-04-13 = 12d old)
INSERT INTO orders VALUES (401, 4, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (402, 4, 1, 1, '2026-04-08', '2026-04-08T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (403, 4, 1, 1, '2026-04-05', '2026-04-05T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (404, 4, 1, 1, '2026-04-03', '2026-04-03T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (405, 4, 1, 1, '2026-04-02', '2026-04-02T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO complaints VALUES (4001, 4, 401, 'restaurant', 1, '2026-04-10', 'wrong', 'open', 'none', 0);
INSERT INTO complaints VALUES (4002, 4, 402, 'restaurant', 1, '2026-04-08', 'cold', 'open', 'none', 0);
INSERT INTO complaints VALUES (4003, 4, 403, 'rider', 1, '2026-04-05', 'late', 'open', 'none', 0);

-- Customer 5: 8 orders, 5 rejected complaints (also fires complaint_rate >=0.50)
INSERT INTO orders VALUES (501, 5, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (502, 5, 1, 1, '2026-04-08', '2026-04-08T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (503, 5, 1, 1, '2026-04-06', '2026-04-06T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (504, 5, 1, 1, '2026-04-04', '2026-04-04T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (505, 5, 1, 1, '2026-04-02', '2026-04-02T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (506, 5, 1, 1, '2026-03-30', '2026-03-30T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (507, 5, 1, 1, '2026-03-25', '2026-03-25T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (508, 5, 1, 1, '2026-03-20', '2026-03-20T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO complaints VALUES (5001, 5, 501, 'restaurant', 1, '2026-04-10', 'fabricated', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (5002, 5, 502, 'restaurant', 1, '2026-04-08', 'fabricated', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (5003, 5, 503, 'restaurant', 1, '2026-04-06', 'fabricated', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (5004, 5, 504, 'restaurant', 1, '2026-04-04', 'fabricated', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (5005, 5, 505, 'restaurant', 1, '2026-04-02', 'fabricated', 'rejected', 'none', 0);

-- Customer 6: 8 orders, 6 complaints (75% rate), 4 refunds in 30d, 3 rejected
INSERT INTO orders VALUES (601, 6, 1, 1, '2026-04-10', '2026-04-10T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (602, 6, 1, 1, '2026-04-08', '2026-04-08T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (603, 6, 1, 1, '2026-04-06', '2026-04-06T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (604, 6, 1, 1, '2026-04-04', '2026-04-04T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (605, 6, 1, 1, '2026-04-02', '2026-04-02T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (606, 6, 1, 1, '2026-03-30', '2026-03-30T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (607, 6, 1, 1, '2026-03-25', '2026-03-25T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO orders VALUES (608, 6, 1, 1, '2026-03-20', '2026-03-20T13:00', 'delivered', 300, 30, 330, 'upi', NULL, 'addr');
INSERT INTO complaints VALUES (6001, 6, 601, 'restaurant', 1, '2026-04-10', 'cold', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (6002, 6, 602, 'restaurant', 1, '2026-04-08', 'wrong', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (6003, 6, 603, 'restaurant', 1, '2026-04-06', 'wrong', 'rejected', 'none', 0);
INSERT INTO complaints VALUES (6004, 6, 604, 'restaurant', 1, '2026-04-04', 'cold', 'resolved', 'credit', 50);
INSERT INTO complaints VALUES (6005, 6, 605, 'restaurant', 1, '2026-04-02', 'cold', 'resolved', 'refund_partial', 100);
INSERT INTO complaints VALUES (6006, 6, 606, 'rider', 1, '2026-03-30', 'late', 'resolved', 'apology', 0);
INSERT INTO refunds VALUES (6101, 6, 601, 100, 'wallet_credit', '2026-04-10', 'cold');
INSERT INTO refunds VALUES (6102, 6, 602, 100, 'wallet_credit', '2026-04-08', 'wrong');
INSERT INTO refunds VALUES (6103, 6, 605, 100, 'wallet_credit', '2026-04-02', 'cold');
INSERT INTO refunds VALUES (6104, 6, 606, 100, 'wallet_credit', '2026-03-30', 'late');

-- Some reviews
INSERT INTO reviews VALUES (1, 1, 101, 1, 5, 'Great', '2026-04-10');
INSERT INTO reviews VALUES (2, 1, 102, 1, 4, 'OK', '2026-04-08');
INSERT INTO reviews VALUES (3, 2, 201, 1, 3, NULL, '2026-04-10');

-- One rider incident, unverified
INSERT INTO rider_incidents VALUES (1, 1, 401, 'late', '2026-04-05', 0, 'customer claim');
