"""
Base Grammar Rules

Common rules used across all grammar modules:
- Identifiers
- Literals (strings, numbers, booleans)
- Basic expressions
- Common operators
"""

BASE_GRAMMAR = r"""
// Base Grammar Rules - Common patterns used across all modules

// Identifiers and Names
identifier: CNAME
CNAME: /[a-zA-Z_][a-zA-Z0-9_]*/
qualified_identifier: identifier ("." identifier)*

// Literals
string: ESCAPED_STRING
ESCAPED_STRING: /"([^"\\]|\\.)*"|'([^'\\]|\\.)*'/
number: SIGNED_NUMBER
SIGNED_NUMBER: /[+-]?(\d+\.?\d*|\.\d+)/
boolean: "true" | "false" | "TRUE" | "FALSE"
null: "null" | "NULL"

// Basic Types
literal: string | number | boolean | null
variable: "$" identifier

// Lists and Arrays
identifier_list: identifier ("," identifier)*
string_list: string ("," string)*
number_list: number ("," number)*

// Property Lists
property_list: property_pair ("," property_pair)*
property_pair: identifier ":" property_value
property_value: literal | variable | "{" property_list "}" | "[" (literal | variable) ("," (literal | variable))* "]"

// Conditions and Expressions
condition: expression
expression: comparison | logical_expression
comparison: qualified_identifier comparison_op (literal | variable | qualified_identifier)
comparison_op: "=" | "!=" | "<>" | "<" | "<=" | ">" | ">=" | "IN" | "NOT" "IN" | "LIKE" | "NOT" "LIKE"
logical_expression: expression logical_op expression
logical_op: "AND" | "OR" | "NOT"

// Order By
order_by_clause: qualified_identifier ("ASC" | "DESC")? ("," qualified_identifier ("ASC" | "DESC")?)*

// Return Clauses
return_clause: "RETURN" return_expression ("," return_expression)*
return_expression: qualified_identifier ("AS" identifier)?

// Parameters
parameter_dict: "{" parameter_pair ("," parameter_pair)* "}"
parameter_pair: identifier ":" parameter_value
parameter_value: literal | variable | "{" parameter_dict "}" | "[" parameter_value ("," parameter_value)* "]"

// Common Keywords (for reference)
%import common.WS
%ignore WS
"""







