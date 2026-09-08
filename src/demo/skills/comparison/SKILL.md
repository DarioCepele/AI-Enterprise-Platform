---
name: comparison
description: Compares several items along common dimensions and renders the result as a table.
---

# Structured comparison

When the user asks to compare two or more things:

1. Identify the **dimensions** of the comparison. If the user named them, use
   those and add none. If they did not, pick three or four that really tell the
   items apart.
2. Call `ui_table` with one column for the dimension and one column for each
   compared item.
3. After the table write two or three lines saying **what actually differs**,
   not repeating the cells.

Do not describe the comparison in words before calling `ui_table`: the user
watches the table appear, and repeating it in prose turns it into noise.
