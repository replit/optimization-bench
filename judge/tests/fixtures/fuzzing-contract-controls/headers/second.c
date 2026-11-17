#include OPTIMBENCH_CONTROL_HEADER
int second_consumer(void)
{
    int (*volatile value)(void) = &contract_value;
    return value();
}
