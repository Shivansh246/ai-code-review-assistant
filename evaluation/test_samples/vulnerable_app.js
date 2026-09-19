const express = require('express');
const app = express();
const sqlite3 = require('sqlite3');

app.use(express.json());

// VULN: hardcoded_credentials, severity=high, lines=8-8
const awsSecret = "AKIAIOSFODNN7EXAMPLE";

app.get('/user', (req, res) => {
    const userId = req.query.id;
    const db = new sqlite3.Database('./app.db');
    // VULN: sql_injection, severity=critical, lines=14-16
    db.all(`SELECT * FROM users WHERE id = ${userId}`, (err, rows) => {
        res.json(rows);
    });
});

app.get('/search', (req, res) => {
    const query = req.query.q;
    // VULN: xss, severity=medium, lines=22-23
    res.send(`<h1>Search results for: ${query}</h1>`);
});

app.post('/merge', (req, res) => {
    const payload = req.body;
    // VULN: prototype_pollution, severity=high, lines=28-32
    const config = {};
    for (let key in payload) {
        config[key] = payload[key];
    }
    res.json(config);
});

app.get('/calc', (req, res) => {
    const math = req.query.math;
    // VULN: insecure_eval, severity=critical, lines=38-39
    const result = eval(math);
    res.send(`Result: ${result}`);
});

// VULN: missing_auth, severity=high, lines=44-46
app.get('/admin/stats', (req, res) => {
    // Missing authentication check
    res.json({ users: 1000, revenue: 50000 });
});

app.listen(3000);
