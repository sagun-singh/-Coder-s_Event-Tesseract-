# Department Inventory & Student Item Issue System

An automated inventory management and item issuing web application designed to manage department laboratory assets, equipment stock, student borrowing records, return deadlines, and renewal requests.

## 📌 Features

### 🏢 Coordinator Management

- **Inventory and Asset Tracking:** Distinguishes between individually tracked assets, such as development boards and laptops, and bulk quantity items, such as USB cables and adapters.
- **Item Issuing and Validation:** Validates student registration, item condition, stock availability, and asset assignment before issuing items.
- **Automatic Due Dates:** Calculates a 15-day borrowing period.
- **Returns and Partial Returns:** Records returned item quantities and conditions, including usable, damaged, lost, or under-maintenance items.
- **Renewal Management:** Allows coordinators to review, approve, or reject student renewal requests. Approved renewals extend the due date by 15 days.
- **Reports and Exports:** Supports department-wise, lab-wise, programme-wise, student-wise, and item-wise reports, with CSV export functionality.

### 🎓 Student Portal

- **Role-Based Access:** Allows students to view their active issued items, due dates, renewal status, and borrowing history.
- **Renewal Requests:** Enables students to request an extension before an item's due date.
- **Due-Date Indicators:** Uses color-coded traffic-light indicators to highlight upcoming and overdue items:
  - 🟢 Green — Upcoming or sufficiently distant due dates.
  - 🟡 Yellow — Due dates approaching.
  - 🔴 Red — Overdue items.

### ⭐ Key Highlight Features

- **Borrower Karma Score:** Starts at 100 points and dynamically reflects borrower behavior, including return punctuality and item condition. Gold, Silver, and Bronze badges represent borrower performance.
- **Demo Time Machine:** Simulates future dates to demonstrate due-date calculations and overdue-item detection.

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application logic and backend processing |
| SQLite3 | Database management |
| HTML5 | Web page structure |

## 📂 Project Structure

```text
.
├── app.py                      # Main application entry point (if present)
├── database.py                 # Database creation and sample data
├── karma.py                    # Borrower Karma Score and badge logic
├── logic_issue.py              # Item issuing and validation rules
├── logic_return_renewal.py     # Return processing and renewal handling
├── logic_reports.py             # Reports, statistics, and overdue detection
├── reports_pages.py             # Reporting and analytics routes
├── student_pages.py             # Student portal routes and functionality
├── README.md                    # Project documentation
└── templates/
    ├── report_table.html        # Report data display
    ├── student_base.html        # Student portal base layout
    ├── student_history.html     # Student borrowing history
    ├── student_home.html        # Student dashboard and active items
    └── student_login.html       # Student login page
```

*Note: The structure above describes the expected project files. Actual filenames and available features may vary as development progresses.*

## 🔐 Security Considerations

- Enforce role-based access controls for students and coordinators.
- Verify student ownership of issued items before processing renewals or displaying borrowing records.
- Validate all user inputs on the server.
- Use parameterized SQL queries rather than constructing SQL statements from user input.
- Use database transactions for related inventory, return, renewal, and audit-log updates.
- Keep passwords securely hashed and never store plaintext passwords in production.
- Do not commit real credentials, secret keys, or sensitive student data to GitHub.
- Restrict access to the database file and back up important records securely.
- Treat demonstration accounts and sample data as development-only resources.

## 🧪 Testing

Before integrating changes into the main application:

1. Verify that invalid inputs are rejected.
2. Test issuing and returning items, including partial returns.
3. Test renewal requests and approval/rejection workflows.
4. Check that unauthorized users cannot perform restricted actions.
5. Verify that database failures do not leave inventory counts or logs inconsistent.
6. Confirm that reports and due-date indicators display the correct information.

## 👥 Team Development

The project is developed collaboratively, with separate modules for database management, issuing logic, return and renewal logic, reporting, student pages, and application integration.

Each module should retain consistent function names, database interfaces, and agreed validation rules so that the components can be integrated reliably.

## 🚀 Future Improvements

Potential improvements include:

- Expanded audit logging and security monitoring.
- Automated reminders for approaching due dates.
- More detailed inventory analytics.
- Additional automated tests.
- Improved accessibility and responsive design.
