; ZyenLang highlights for Zed

(line_comment) @comment
(string) @string
(number) @number
(boolean) @boolean

[
  "fn"
  "let"
  "const"
  "set"
  "return"
  "if"
  "else"
  "for"
  "struct"
  "class"
  "import"
  "as"
  "pass"
  "break"
] @keyword

[
  "->"
  "="
  "+"
  "-"
  "*"
  "/"
  "%"
  "=="
  "!="
  "<"
  "<="
  ">"
  ">="
  "&&"
  "||"
  "!"
  "&"
] @operator

[
  "("
  ")"
  "{"
  "}"
  "["
  "]"
] @punctuation.bracket

[
  ";"
  ","
  ":"
  "."
] @punctuation.delimiter

(primitive_type) @type.builtin
(type_identifier) @type

(function_definition name: (identifier) @function)
(parameter name: (identifier) @variable.parameter)
(call_expression function: (identifier) @function)
(call_expression function: (field_expression field: (identifier) @function.method))
(struct_definition name: (identifier) @type)
(class_definition name: (identifier) @type)
(field_declaration name: (identifier) @property)
(field_expression field: (identifier) @property)

(import_path) @string.special
(namespace_identifier) @module
